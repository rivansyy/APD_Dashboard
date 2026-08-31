# --- WAJIB DI BARIS PALING ATAS (SEBELUM IMPORT CV2) ---
import os
os.environ['OPENCV_LOG_LEVEL'] = 'OFF'
os.environ['OPENCV_FFMPEG_LOGLEVEL'] = '-8'
os.environ["FFMPEG_LOG_LEVEL"] = "-8"

# --- PERBAIKAN OPTIMASI FFMPEG UNTUK RTSP H.265 / UDP & TCP ---
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;tcp"         # Diubah ke TCP agar aliran paket data CCTV lebih stabil
    "|stimeout;10000000"         # Timeout socket dinaikkan jadi 10 detik
    "|max_delay;1000000"         # Toleransi jitter buffer 1 detik
    "|framedrop"                 # Buang frame otomatis jika decoder terlambat
    "|ec;explode"                # Error concealment
    "|err_detect;explode"        # Minta FFmpeg langsung drop frame error
    "|fflags;nobuffer+discardcorrupt" # Abalkan dan buang paket data yang korup
)

# --- IMPORT LAINNYA ---
import cv2
import torch
from ultralytics import YOLO
from datetime import datetime
import pytz
import numpy as np
from pathlib import Path
from boxmot import BoTSORT  # Menggunakan BoTSORT dari BoxMOT
import threading
import queue
import time
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

# ==============================================================================
# --- LAPISAN PENGAMAN TAMBAHAN: ALIHKAN LOG MENTAH LIBAV/FFMPEG KE FILE ---
# ==============================================================================
try:
    _file_log_ffmpeg = open("ffmpeg_decoder.log", "a", buffering=1)
    os.dup2(_file_log_ffmpeg.fileno(), sys.stderr.fileno())
    print(f"[INFO] Log internal decoder FFmpeg/libav dialihkan ke file 'ffmpeg_decoder.log'.")
except Exception as e:
    print(f"[PERINGATAN] Gagal mengalihkan log FFmpeg ke file, Error: {e}")

# ==============================================================================
# --- KONFIGURASI MULTI-KAMERA / VIDEO WORKSHOP ---
# ==============================================================================
DAFTAR_KAMERA = []
_idx_kamera = 1
while True:
    _url_kamera = os.getenv(f"KAMERA_{_idx_kamera}_URL")
    if not _url_kamera:
        break
    DAFTAR_KAMERA.append({
        "url": _url_kamera,
        "nama": os.getenv(f"KAMERA_{_idx_kamera}_NAMA", f"Kamera_{_idx_kamera}")
    })
    _idx_kamera += 1

if not DAFTAR_KAMERA:
    print("[PERINGATAN] Tidak ada kamera terkonfigurasi di .env (KAMERA_1_URL, KAMERA_1_NAMA, dst).")

API_DASHBOARD_URL = os.getenv("API_DASHBOARD_URL", "http://localhost:5000/api/events")
STREAM_PORT = int(os.getenv("STREAM_PORT", 5001))

# --- SETUP MINI WEB SERVER (FLASK) UNTUK LIVE STREAMING KE DASHBOARD ---
from flask import Flask, Response, send_from_directory

flask_app = Flask(__name__)

@flask_app.after_request
def izinkan_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response

latest_frames = {}          # Menyimpan frame terbaru tiap kamera (hasil deteksi + bounding box)
latest_frames_lock = threading.Lock()

def generate_mjpeg(nama_kamera):
    """Generator yang terus-menerus kirim frame terbaru sebagai gambar JPEG berurutan (MJPEG)."""
    while True:
        with latest_frames_lock:
            frame = latest_frames.get(nama_kamera)
        if frame is not None:
            ok, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if ok:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        time.sleep(0.04)  # Batasi ~25 fps supaya tidak membebani jaringan/browser

@flask_app.route('/stream/<nama_kamera>')
def stream(nama_kamera):
    return Response(generate_mjpeg(nama_kamera),
                     mimetype='multipart/x-mixed-replace; boundary=frame')

@flask_app.route('/snapshot/<path:nama_file>')
def serve_snapshot(nama_file):
    return send_from_directory(os.path.abspath(folder_simpan), nama_file)

@flask_app.route('/cameras')
def list_cameras():
    return {"cameras": [k["nama"] for k in DAFTAR_KAMERA]}

def jalankan_flask_server():
    flask_app.run(host='0.0.0.0', port=STREAM_PORT, threaded=True, use_reloader=False)

# --- PENGATURAN AMBANG BATAS KEMIRIPAN / CONFIDENCE DETEKSI OBJEK ---
AMBANG_BATAS_DETEKSI = {
    "person": 0.30,
    "safety_helm": 0.70,
    "vest": 0.70,
    "safety_shoes": 0.60,
    "glasses": 0.50,
    "gloves": 0.70,
    "no_safety_helm": 0.70,
    "no_vest": 0.70,
    "no_safety_shoes": 0.70,
    "no_glasses": 0.70,
    "no_gloves": 0.70
}

# --- PREPARASI FOLDER SNAPSHOT ---
folder_simpan = "Pelanggaran"
os.makedirs(folder_simpan, exist_ok=True)

folder_simpan_manual = "Snapshot_Manual"
os.makedirs(folder_simpan_manual, exist_ok=True)

# 1. Pengecekan Device
if torch.cuda.is_available():
    device_pilihan = 0
    nama_gpu = torch.cuda.get_device_name(0)
    print(f"\n Sukses: Program dijalankan MENGGUNAKAN GPU -> {nama_gpu}\n")
else:
    device_pilihan = 'cpu'
    print("\n Peringatan: CUDA tidak terdeteksi! Program dijalankan MENGGUNAKAN CPU.\n")

# 2. Inisialisasi model YOLO
model = YOLO("best1280.pt")
model.to(torch.device(device_pilihan))

# Set zona waktu WITA
wita_tz = pytz.timezone('Asia/Makassar')

# --- QUEUE ASYNC IMAGE SAVER ---
queue_snapshot = queue.Queue()

def worker_penyimpan_gambar():
    """Thread khusus untuk menyimpan gambar di background tanpa memblokir stream video"""
    while True:
        item = queue_snapshot.get()
        if item is None:
            break
        path_lengkap, frame_data, nama_jendela = item
        try:
            cv2.imwrite(path_lengkap, frame_data)
            print(f"[{nama_jendela}] (Async Save) Capture disimpan -> {path_lengkap}")
        except Exception as e:
            print(f"Gagal menyimpan capture dari {nama_jendela}. Error: {e}")
        queue_snapshot.task_done()

thread_saver = threading.Thread(target=worker_penyimpan_gambar, daemon=True)
thread_saver.start()

# --- QUEUE ASYNC PENGIRIM EVENT KE DASHBOARD ---
queue_event = queue.Queue()

def worker_pengirim_event():
    """Thread khusus untuk kirim data pelanggaran ke Express, tanpa memblokir stream video."""
    while True:
        item = queue_event.get()
        if item is None:
            break
        payload = item
        try:
            requests.post(API_DASHBOARD_URL, json=payload, timeout=3)
            print(f"[{payload['camera_name']}] Event terkirim ke dashboard.")
        except Exception as e:
            print(f"[PERINGATAN] Gagal kirim event ke dashboard: {e}")
        queue_event.task_done()

thread_event_sender = threading.Thread(target=worker_pengirim_event, daemon=True)
thread_event_sender.start()

# --- JALANKAN FLASK SERVER DI THREAD TERPISAH (UNTUK LIVE STREAMING) ---
thread_flask = threading.Thread(target=jalankan_flask_server, daemon=True)
thread_flask.start()
print(f"[INFO] Live stream server aktif di http://localhost:{STREAM_PORT}/stream/<nama_kamera>")


def hitung_iou(boxA, boxB):
    """Menghitung Intersection-over-Union antara 2 bounding box (x1,y1,x2,y2)."""
    xA = max(boxA[0], boxB[0]); yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2]); yB = min(boxA[3], boxB[3])
    inter = max(0, xB - xA) * max(0, yB - yA)
    areaA = max(0, boxA[2] - boxA[0]) * max(0, boxA[3] - boxA[1])
    areaB = max(0, boxB[2] - boxB[0]) * max(0, boxB[3] - boxB[1])
    return inter / float(areaA + areaB - inter + 1e-6)


def siapkan_poligon_roi(roi_config):
    if not roi_config:
        return None

    titik_titik = list(roi_config)

    if len(titik_titik) == 2:
        (x1, y1), (x2, y2) = titik_titik
        x_kiri, x_kanan = min(x1, x2), max(x1, x2)
        y_atas, y_bawah = min(y1, y2), max(y1, y2)
        poligon = np.array([
            [x_kiri, y_atas],
            [x_kanan, y_atas],
            [x_kanan, y_bawah],
            [x_kiri, y_bawah]
        ], dtype=np.int32)
    else:
        poligon = np.array(titik_titik, dtype=np.int32)

    return poligon


def titik_di_dalam_roi(titik_x, titik_y, poligon_roi):
    if poligon_roi is None:
        return True
    hasil = cv2.pointPolygonTest(poligon_roi, (float(titik_x), float(titik_y)), False)
    return hasil >= 0


def is_frame_korup(frame, std_threshold=3.0):
    """Pengecekan mendasar untuk memastikan frame tidak kosong atau rusak total."""
    try:
        if frame is None or frame.size == 0:
            return True
        kecil = cv2.resize(frame, (32, 32), interpolation=cv2.INTER_NEAREST)
        return kecil.std() < std_threshold
    except Exception:
        return True


def proses_kamera(rtsp_url, nama_jendela, buffer_size=1, roi_config=None):
    poligon_roi = siapkan_poligon_roi(roi_config)
    if poligon_roi is not None:
        print(f"[{nama_jendela}] ROI aktif -> hanya area {roi_config} yang diproses.")
    else:
        print(f"[{nama_jendela}] ROI tidak dikonfigurasi -> seluruh area frame diproses.")

    # --- INISIALISASI BOXMOT TRACKER (BoTSORT) ---
    try:
        print(f"[{nama_jendela}] Inisialisasi BoxMOT (BoTSORT)...")
        tracker = BoTSORT(
            reid_weights=Path('osnet_x1_0_msmt17.pth'),
            device=torch.device(device_pilihan),
            half=False,
            track_high_thresh=0.50,
            new_track_thresh=0.60,
            match_thresh=0.80,
            cmc_method="sparseOptFlow"  # GMC: Kompensasi pergerakan/getaran kamera
        )
        print(f"[{nama_jendela}] BoxMOT BoTSORT Tracker Berhasil Dimuat.")
    except Exception as e:
        print(f"[{nama_jendela}] [Eror] Gagal memuat BoxMOT: {e}")
        return

    thread_hidup = True

    KELAS_POSITIF = ['safety_helm', 'vest', 'safety_shoes', 'glasses', 'gloves']
    KELAS_NEGATIF = ['no_safety_helm', 'no_vest', 'no_safety_shoes', 'no_glasses', 'no_gloves']

    lacak_pekerja = {}
    DURASI_STABIL_SNAPSHOT = 10.0
    TIMEOUT_ID_HILANG = 30.0  # detik; ID yang tak terlihat lebih lama dari ini dianggap benar-benar pergi

    while thread_hidup:
        print(f"[{nama_jendela}] Mencoba menghubungkan ke stream/file...")
        
        cap = None
        mode_decode = "N/A"

        if isinstance(rtsp_url, str) and rtsp_url.startswith("rtsp"):
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            mode_decode = "Software (FFmpeg Optimized)"
        else:
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_DSHOW if isinstance(rtsp_url, int) else cv2.CAP_ANY)
            mode_decode = "File Video / Local Capture"

        cap.set(cv2.CAP_PROP_BUFFERSIZE, buffer_size)

        if not cap.isOpened():
            print(f"[EROR] Sumber {nama_jendela} tidak dapat diakses. Mencoba ulang dalam 5 detik...")
            time.sleep(5)
            continue

        fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
        fourcc_str = "".join([chr((fourcc_int >> 8 * i) & 0xFF) for i in range(4)]).strip() or "?"
        print(f"[{nama_jendela}] Decode mode: {mode_decode} | Codec terdeteksi: {fourcc_str}")

        frame_terbaru = None
        koneksi_aktif = True
        lock_frame = threading.Lock()

        # --- THREAD GRABBER FRAME TAHAN BANTING ---
        def grab_frame_worker():
            nonlocal frame_terbaru, koneksi_aktif
            
            is_file_video = isinstance(rtsp_url, str) and not rtsp_url.startswith("rtsp")
            fps_video = cap.get(cv2.CAP_PROP_FPS) if is_file_video else 0
            delay_frame = (1.0 / fps_video) if (fps_video and fps_video > 0) else 0.01

            gagal_beruntun = 0
            MAX_GAGAL_BERUNTUN = 300  # Nilai lebih tinggi agar toleran terhadap lag RTSP/Wi-Fi

            while koneksi_aktif:
                ret, frame_baca = cap.read()

                # --- 1. JIKA FRAME GAGAL BACA / VIDEO HABIS ---
                if not ret or frame_baca is None:
                    if is_file_video:
                        # Jika file MP4 habis, reset kembali ke frame 0 (LOOPING)
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        time.sleep(0.05)
                        continue
                    else:
                        gagal_beruntun += 1
                        if gagal_beruntun >= MAX_GAGAL_BERUNTUN:
                            print(f"\n[PERINGATAN] {nama_jendela} Sinyal terputus setelah {MAX_GAGAL_BERUNTUN} attempt!")
                            koneksi_aktif = False
                            break
                        time.sleep(0.02)
                        continue

                # --- 2. PENGECEKAN KORUPSI FRAME SANGAT DASAR ---
                if is_frame_korup(frame_baca):
                    gagal_beruntun += 1
                    if gagal_beruntun >= MAX_GAGAL_BERUNTUN:
                        print(f"\n[PERINGATAN] {nama_jendela} Terlalu banyak frame rusak/kosong!")
                        koneksi_aktif = False
                        break
                    time.sleep(0.01)
                    continue

                # --- 3. JIKA FRAME VALID ---
                gagal_beruntun = 0
                with lock_frame:
                    frame_terbaru = frame_baca.copy()

                if is_file_video:
                    time.sleep(delay_frame)
                else:
                    time.sleep(0.001)

        thread_grabber = threading.Thread(target=grab_frame_worker, daemon=True)
        thread_grabber.start()

        print(f"[{nama_jendela}] Stream berhasil terhubung! Memulai pemrosesan...")
        lebar_asli = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        tinggi_asli = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"[{nama_jendela}] Resolusi Asli Terdeteksi: {lebar_asli}x{tinggi_asli}")

        cv2.namedWindow(nama_jendela, cv2.WINDOW_NORMAL)
        if lebar_asli > 1920:
            cv2.resizeWindow(nama_jendela, 1280, 720)
        else:
            cv2.resizeWindow(nama_jendela, lebar_asli, tinggi_asli)

        while koneksi_aktif:
            with lock_frame:
                if frame_terbaru is None:
                    time.sleep(0.01)
                    continue
                frame = frame_terbaru.copy()

            tinggi_frame_kamera, lebar_frame = frame.shape[:2]
            waktu_objek_sekarang = datetime.now(wita_tz)
            waktu_sekarang_detik = time.time()

            frame_asli_sebelum_anotasi = frame.copy()

            # --- PREDIKSI DETEKSI YOLO ---
            results = model(frame, verbose=False, device=device_pilihan)

            total_pekerja_apd = 0
            total_pekerja_apd_tidak_lengkap = 0
            total_pekerja_no_apd = 0

            pending_snapshots = []
            box_render_cache = {}

            if results[0].boxes is not None:
                boxes_xyxy = results[0].boxes.xyxy.cpu().numpy()
                confidences = results[0].boxes.conf.cpu().numpy()
                class_ids = results[0].boxes.cls.cpu().numpy()
                names = model.names

                deteksi_person_boxmot = []
                daftar_apd = []

                for idx, (box, conf, cls) in enumerate(zip(boxes_xyxy, confidences, class_ids)):
                    nama_kelas = names[int(cls)].lower()
                    limit_conf = AMBANG_BATAS_DETEKSI.get(nama_kelas, 0.35)
                    
                    if nama_kelas == 'person':
                        if conf >= limit_conf:
                            titik_kaki_x = (box[0] + box[2]) / 2.0
                            titik_kaki_y = box[3]
                            if titik_di_dalam_roi(titik_kaki_x, titik_kaki_y, poligon_roi):
                                deteksi_person_boxmot.append({'box': box, 'conf': conf, 'cls': cls})

                    elif nama_kelas in KELAS_POSITIF or nama_kelas in KELAS_NEGATIF:
                        if conf >= limit_conf:
                            is_pos = nama_kelas in KELAS_POSITIF
                            daftar_apd.append({'jenis': nama_kelas, 'box': box, 'is_positive': is_pos})

                # --- TRACKING DENGAN BOXMOT (BoTSORT) ---
                daftar_person = []
                if len(deteksi_person_boxmot) > 0:
                    person_boxes = np.array([p['box'] for p in deteksi_person_boxmot])
                    person_confs = np.array([p['conf'] for p in deteksi_person_boxmot])
                    person_clss = np.array([p['cls'] for p in deteksi_person_boxmot])

                    deteksi_input = np.column_stack((person_boxes, person_confs, person_clss))
                    tracks = tracker.update(deteksi_input, frame)
                    
                    for trk in tracks:
                        px1, py1, px2, py2, tracking_id, cls_id, conf_track, ind = trk
                        box = [px1, py1, px2, py2]
                        t_id = int(tracking_id)
                        daftar_person.append({'box': box, 'conf': conf_track, 'cls': cls_id, 'id': t_id})

                AMBANG_IOU_OVERLAP = 0.25
                semua_box_person_frame_ini = [p['box'] for p in daftar_person]

                for person in daftar_person:
                    px1, py1, px2, py2 = person['box']
                    person_id = person['id']
                    
                    posisi_kaki_y = int(py2)
                    tinggi_total_person = py2 - py1
                    lebar_total_person = max(1, px2 - px1)
                    rasio_aspek = tinggi_total_person / lebar_total_person

                    jarak_ke_tepi_bawah = tinggi_frame_kamera - posisi_kaki_y
                    
                    is_terpotong = jarak_ke_tepi_bawah < 10 or rasio_aspek < 0.9
                    is_jarak_jauh_ketat = (tinggi_total_person < 70) and (tinggi_total_person >= 30)

                    box_ini_overlap_check = (px1, py1, px2, py2)
                    is_overlap_ringan = any(
                        hitung_iou(box_ini_overlap_check, box_lain) > AMBANG_IOU_OVERLAP
                        for box_lain in semua_box_person_frame_ini
                        if not np.array_equal(box_lain, box_ini_overlap_check)
                    )

                    kondisi_tunda = is_terpotong or is_jarak_jauh_ketat or is_overlap_ringan
                    if is_terpotong:
                        status_tunda = "TUBUH TERPOTONG"
                    elif is_jarak_jauh_ketat:
                        status_tunda = "JARAK JAUH"
                    elif is_overlap_ringan:
                        status_tunda = "TUMPANG TINDIH"
                    else:
                        status_tunda = None

                    bx1, by1, bx2, by2 = int(px1), int(py1), int(px2), int(py2)

                    if kondisi_tunda:
                        box_render_cache[person_id] = {
                            "box_person": (bx1, by1, bx2, by2),
                            "status": status_tunda,
                            "list_apd": [],
                            "durasi": 0,
                            "snapshot_diambil": False
                        }
                        # CATATAN: entry lacak_pekerja[person_id] SENGAJA TIDAK dihapus di sini.
                        # Kondisi tunda ini seringkali cuma sekilas (1-2 frame) akibat jitter
                        # bounding box / overlap tipis dengan objek lain. Kalau entry dihapus,
                        # 'waktu_mulai' dan 'snapshot_diambil' ikut reset padahal status APD
                        # orang ini tidak berubah -> snapshot terpicu berulang untuk ID yang sama.
                        # Timer & flag snapshot cukup "dibekukan" sementara (tidak diupdate),
                        # lalu lanjut normal begitu kondisi tunda hilang. 'terakhir_terlihat' tetap
                        # diperbarui supaya ID ini tidak ikut dibersihkan oleh cleanup di bawah.
                        if person_id in lacak_pekerja:
                            lacak_pekerja[person_id]['terakhir_terlihat'] = waktu_sekarang_detik
                        continue

                    # --- DETEKSI KELENGKAPAN APD PEKERJA ---
                    list_apd_terdeteksi = []
                    deteksi_apd = {'safety_helm': False, 'glasses': False, 'gloves': False, 'vest': False, 'safety_shoes': False}

                    terdeteksi_status_glasses = False
                    terdeteksi_status_gloves = False

                    for apd in daftar_apd:
                        ax1, ay1, ax2, ay2 = apd['box']
                        amx = (ax1 + ax2) / 2
                        amy = (ay1 + ay2) / 2
                        
                        pad_x = (px2 - px1) * 0.15
                        pad_y = (py2 - py1) * 0.10

                        if ((px1 - pad_x) <= amx <= (px2 + pad_x)) and ((py1 - pad_y) <= amy <= (py2 + pad_y)):
                            jns = apd['jenis']

                            if jns in ['glasses', 'no_glasses']:
                                terdeteksi_status_glasses = True
                            if jns in ['gloves', 'no_gloves']:
                                terdeteksi_status_gloves = True

                            if jns in deteksi_apd:
                                deteksi_apd[jns] = True

                            list_apd_terdeteksi.append({
                                'box': (int(ax1), int(ay1), int(ax2), int(ay2)),
                                'is_positive': apd['is_positive']
                            })

                    jumlah_apd_positif = sum(deteksi_apd.values())

                    if jumlah_apd_positif == 5:
                        status_saat_ini = "APD LENGKAP"
                        total_pekerja_apd += 1
                    elif 1 <= jumlah_apd_positif < 5:
                        status_saat_ini = "APD TIDAK LENGKAP"
                        total_pekerja_apd_tidak_lengkap += 1
                    else:
                        status_saat_ini = "TIDAK MENGGUNAKAN APD"
                        total_pekerja_no_apd += 1

                    # --- LOGIKA 2 (TAMBAHAN): VALIDASI TAG DETAIL GLASSES & GLOVES ---
                    # Berlaku untuk status "APD TIDAK LENGKAP" dan "TIDAK MENGGUNAKAN APD":
                    # selama tag glasses/no_glasses ATAU tag gloves/no_gloves belum pernah
                    # terdeteksi sama sekali pada pekerja ini, snapshot ditunda dulu.
                    if status_saat_ini in ["APD TIDAK LENGKAP", "TIDAK MENGGUNAKAN APD"]:
                        logika_detail_terpenuhi = terdeteksi_status_glasses and terdeteksi_status_gloves
                    else:
                        # status_saat_ini == "APD LENGKAP" -> kelima tag positif pasti sudah terdeteksi
                        logika_detail_terpenuhi = True

                    if person_id not in lacak_pekerja:
                        lacak_pekerja[person_id] = {
                            'status': status_saat_ini,
                            'waktu_mulai': waktu_sekarang_detik,
                            'snapshot_diambil': False,
                            'terakhir_terlihat': waktu_sekarang_detik
                        }
                    else:
                        if lacak_pekerja[person_id]['status'] != status_saat_ini:
                            lacak_pekerja[person_id]['status'] = status_saat_ini
                            lacak_pekerja[person_id]['waktu_mulai'] = waktu_sekarang_detik
                            lacak_pekerja[person_id]['snapshot_diambil'] = False
                        # ID ini terlihat normal di frame ini -> tandai waktunya, dipakai cleanup di bawah
                        lacak_pekerja[person_id]['terakhir_terlihat'] = waktu_sekarang_detik

                    durasi_stabil = waktu_sekarang_detik - lacak_pekerja[person_id]['waktu_mulai']

                    # --- PEMICU SNAPSHOT (Logika 1 AND Logika 2) ---
                    # Logika 1: status sudah stabil >= DURASI_STABIL_SNAPSHOT detik
                    # Logika 2: tag detail glasses & gloves sudah terdeteksi (lihat logika_detail_terpenuhi di atas)
                    if (durasi_stabil >= DURASI_STABIL_SNAPSHOT) and logika_detail_terpenuhi and not lacak_pekerja[person_id]['snapshot_diambil']:
                        timestamp_auto = waktu_objek_sekarang.strftime("%Y%m%d_%H%M%S")
                        kategori_filename = status_saat_ini.replace(" ", "_")
                        nama_file_auto = f"{nama_jendela}_ID{person_id}_{kategori_filename}_{timestamp_auto}.jpg"
                        path_lengkap_auto = os.path.join(folder_simpan, nama_file_auto)

                        pending_snapshots.append((path_lengkap_auto, person_id, status_saat_ini))
                        lacak_pekerja[person_id]['snapshot_diambil'] = True

                    box_render_cache[person_id] = {
                        "box_person": (bx1, by1, bx2, by2),
                        "status": status_saat_ini,
                        "list_apd": list_apd_terdeteksi,
                        "durasi": int(durasi_stabil),
                        "snapshot_diambil": lacak_pekerja[person_id]['snapshot_diambil']
                    }

            # --- CLEANUP ID YANG SUDAH BENAR-BENAR HILANG (penting untuk program 24/7) ---
            # Berbeda dari kondisi tunda (yang sekarang tidak lagi menghapus entry), ini
            # membersihkan ID yang memang sudah tidak terlihat sama sekali (orang pergi dari
            # kamera, atau ID lama tidak pernah dipakai lagi) selama lebih dari TIMEOUT_ID_HILANG
            # detik. Tanpa ini, dict lacak_pekerja akan terus membesar tanpa batas.
            id_kadaluarsa = [
                pid for pid, info in lacak_pekerja.items()
                if waktu_sekarang_detik - info.get('terakhir_terlihat', 0) > TIMEOUT_ID_HILANG
            ]
            for pid in id_kadaluarsa:
                del lacak_pekerja[pid]

            # ==============================================================================
            # --- VISUALISASI ROI & BOUNDING BOX ---
            # ==============================================================================
            if poligon_roi is not None:
                cv2.polylines(frame, [poligon_roi], isClosed=True, color=(255, 0, 255), thickness=2)
                cv2.putText(frame, "AREA WAJIB APD", (int(poligon_roi[0][0]) + 5, int(poligon_roi[0][1]) + 20),
                            cv2.FONT_HERSHEY_DUPLEX, 0.5, (255, 0, 255), 1, cv2.LINE_AA)

            for p_id, info in box_render_cache.items():
                bx1, by1, bx2, by2 = info["box_person"]
                stat = info["status"]

                rx1 = max(0, bx1 - 10)
                ry1 = max(0, by1 - 30)
                rx2 = min(lebar_frame, bx2 + 10)
                ry2 = min(tinggi_frame_kamera, by2 + 10)
                if rx2 > rx1 and ry2 > ry1:
                    frame[ry1:ry2, rx1:rx2] = frame_asli_sebelum_anotasi[ry1:ry2, rx1:rx2]

                # --- PENENTUAN LABEL COUNTING / SNAPSHOT ---
                if info.get('snapshot_diambil', False):
                    teks_durasi = "SNAPSHOT"
                else:
                    teks_durasi = f"{info['durasi']}s"

                if stat in ["TUBUH TERPOTONG", "JARAK JAUH", "TUMPANG TINDIH"]:
                    warna_box = (255, 255, 255)
                    label_status = f"STATUS: {stat}"
                elif stat == "APD LENGKAP":
                    warna_box = (0, 255, 0)
                    label_status = f"ID:{p_id} | STATUS: {stat} ({teks_durasi})"
                elif stat == "APD TIDAK LENGKAP":
                    warna_box = (0, 165, 255)
                    label_status = f"ID:{p_id} | STATUS: {stat} ({teks_durasi})"
                else:
                    warna_box = (0, 0, 255)
                    label_status = f"ID:{p_id} | STATUS: {stat} ({teks_durasi})"

                for apd_item in info["list_apd"]:
                    abox = apd_item['box']
                    warna_apd_box = (255, 255, 255) if apd_item['is_positive'] else (0, 255, 255)
                    cv2.rectangle(frame, (abox[0], abox[1]), (abox[2], abox[3]), warna_apd_box, 1)

                cv2.rectangle(frame, (bx1, by1), (bx2, by2), warna_box, 2)
                cv2.putText(frame, label_status, (bx1 + 5, by1 - 8), cv2.FONT_HERSHEY_DUPLEX, 0.35, warna_box, 1, cv2.LINE_AA)

            # ==============================================================================
            # --- OVERLAY PANEL REKAP STATISTIK ---
            # ==============================================================================
            total_pekerja = total_pekerja_apd + total_pekerja_apd_tidak_lengkap + total_pekerja_no_apd

            data_rekap = [
                ("Total Pekerja", total_pekerja, (255, 255, 255)),
                ("Pekerja APD Lengkap", total_pekerja_apd, (0, 255, 0)),
                ("Pekerja APD Tidak Lengkap", total_pekerja_apd_tidak_lengkap, (0, 165, 255)),
                ("Pekerja NO APD", total_pekerja_no_apd, (0, 0, 255))
            ]

            x_label = 20
            x_titik_dua = 300
            y_awal = 35
            jarak_y = 28

            def putTextWithShadow(img, text, pos, font, scale, color, thickness, shadow_color=(0,0,0), offset=(2, 2)):
                shadow_pos = (pos[0] + offset[0], pos[1] + offset[1])
                cv2.putText(img, text, shadow_pos, font, scale, shadow_color, thickness, cv2.LINE_AA)
                cv2.putText(img, text, pos, font, scale, color, thickness, cv2.LINE_AA)

            for idx, (label, nilai, warna) in enumerate(data_rekap):
                y_pos = y_awal + (idx * jarak_y)
                putTextWithShadow(frame, label, (x_label, y_pos),
                                  cv2.FONT_HERSHEY_DUPLEX, 0.5, warna, 1)
                putTextWithShadow(frame, f": {nilai}", (x_titik_dua, y_pos),
                                  cv2.FONT_HERSHEY_DUPLEX, 0.5, warna, 1)

            for path_auto, p_id, st_saat_ini in pending_snapshots:
                queue_snapshot.put((path_auto, frame.copy(), nama_jendela))
                queue_event.put({
                    "camera_name": nama_jendela,
                    "person_id": p_id,
                    "status": st_saat_ini,
                    "snapshot_path": os.path.basename(path_auto),
                    "detected_at": waktu_objek_sekarang.strftime("%Y-%m-%d %H:%M:%S")
                })
                print(f"[{nama_jendela}] STATUS STABIL 10s ({st_saat_ini}) ID:{p_id} -> Snapshot disimpan!")

            frame_untuk_snapshot_manual = frame.copy()

            with latest_frames_lock:
                latest_frames[nama_jendela] = frame.copy()

            cv2.imshow(nama_jendela, frame)

            tombol_ditekan = cv2.waitKey(1) & 0xFF

            if tombol_ditekan == ord('q'):
                thread_hidup = False  
                koneksi_aktif = False
                break
            elif tombol_ditekan == ord('s'):
                timestamp_manual = waktu_objek_sekarang.strftime("%Y%m%d_%H%M%S")
                nama_file_manual = f"{nama_jendela}_manual_{timestamp_manual}.jpg"
                path_lengkap_manual = os.path.join(folder_simpan_manual, nama_file_manual)
                queue_snapshot.put((path_lengkap_manual, frame_untuk_snapshot_manual, nama_jendela))
                print(f"[{nama_jendela}] Snapshot manual diminta (tombol 's') -> {path_lengkap_manual}")

        koneksi_aktif = False
        thread_grabber.join()
        cap.release()

        if not thread_hidup:
            cv2.destroyWindow(nama_jendela)
        else:
            time.sleep(5)

# --- JALANKAN THREAD MULTI-KAMERA ---
threads = []
for kam in DAFTAR_KAMERA:
    buffer_size_kamera = kam.get("buffer_size", 1)
    roi_kamera = kam.get("roi", None)
    t = threading.Thread(target=proses_kamera, args=(kam["url"], kam["nama"], buffer_size_kamera, roi_kamera), daemon=True)
    threads.append(t)
    t.start()

for t in threads:
    t.join()

queue_snapshot.put(None)
thread_saver.join()

queue_event.put(None)
thread_event_sender.join()

cv2.destroyAllWindows()