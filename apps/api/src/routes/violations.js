import express from 'express';
import pool from '../db/connection.js';

const router = express.Router();

// Endpoint untuk MENERIMA data pelanggaran dari skrip Python
router.post('/', async (req, res) => {
  const { camera_name, person_id, status, snapshot_path, detected_at } = req.body;

  if (!camera_name || !person_id || !status) {
    return res.status(400).json({ error: 'Data tidak lengkap' });
  }

  try {
    const [result] = await pool.query(
      `INSERT INTO violations (camera_name, person_id, status, snapshot_path, detected_at) VALUES (?, ?, ?, ?, ?)`,
      [camera_name, person_id, status, snapshot_path, detected_at]
    );

    const dataBaru = {
      id: result.insertId,
      camera_name, person_id, status, snapshot_path, detected_at,
      created_at: new Date().toISOString()
    };

    const io = req.app.get('io');
    io.emit('new_violation', dataBaru); // siarkan ke semua dashboard yang terbuka

    res.status(201).json({ message: 'Tersimpan', id: result.insertId });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: 'Gagal menyimpan ke database' });
  }
});

// Endpoint untuk MENAMPILKAN semua data pelanggaran (dipakai dashboard nanti)
router.get('/', async (req, res) => {
  try {
    const [rows] = await pool.query('SELECT * FROM violations ORDER BY detected_at DESC');
    res.json(rows);
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: 'Gagal mengambil data' });
  }
});

router.get('/stats', async (req, res) => {
  try {
    const [rows] = await pool.query(
      `SELECT status, COUNT(*) as jumlah FROM violations WHERE DATE(detected_at) = CURDATE() GROUP BY status`
    );
    res.json(rows);
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: 'Gagal mengambil statistik' });
  }
});

export default router;