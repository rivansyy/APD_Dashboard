'use client';

import { useEffect, useState } from 'react';
import { io } from 'socket.io-client';

type Violation = {
  id: number;
  camera_name: string;
  person_id: number;
  status: string;
  snapshot_path: string;
  detected_at: string;
  created_at: string;
};

const statusTone: Record<string, string> = {
  'APD TIDAK LENGKAP': '#F5B700',
  'TIDAK MENGGUNAKAN APD': '#E5484D',
  'NO APD': '#E5484D',
};

function waktuRelatif(iso: string) {
  const detik = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (detik < 60) return 'baru saja';
  const menit = Math.floor(detik / 60);
  if (menit < 60) return `${menit} menit lalu`;
  const jam = Math.floor(menit / 60);
  if (jam < 24) return `${jam} jam lalu`;
  return `${Math.floor(jam / 24)} hari lalu`;
}

export default function ViolationsTable({ initialViolations }: { initialViolations: Violation[] }) {
  const [violations, setViolations] = useState<Violation[]>(initialViolations);

  useEffect(() => {
    const socket = io(process.env.NEXT_PUBLIC_API_URL);
    socket.on('new_violation', (data: Violation) => {
      setViolations((prev) => [data, ...prev]);
    });
    return () => { socket.disconnect(); };
  }, []);

  if (violations.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-[#2A2F38] px-6 py-10 text-center text-[#8A8F98]">
        Belum ada insiden tercatat. Baris baru akan muncul di sini otomatis saat terdeteksi.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {violations.map((v) => (
        <div key={v.id} className="flex items-center gap-4 rounded-lg border border-[#2A2F38] bg-[#1C2128] px-4 py-3">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={`${process.env.NEXT_PUBLIC_STREAM_URL}/snapshot/${v.snapshot_path}`}
            alt={`Cuplikan pelanggaran ${v.status}`}
            className="w-16 h-16 object-cover rounded border border-[#2A2F38] flex-shrink-0"
          />
          <div className="flex-1 min-w-0">
            <span
              className="inline-block px-2 py-0.5 rounded text-xs font-medium mb-1"
              style={{
                color: statusTone[v.status] ?? '#B8BCC4',
                backgroundColor: `${statusTone[v.status] ?? '#8A8F98'}1A`,
              }}
            >
              {v.status}
            </span>
            <p className="text-sm text-[#B8BCC4]">
              {v.camera_name} · Pekerja #{v.person_id}
            </p>
          </div>
          <span className="text-xs font-[family-name:var(--font-mono)] text-[#5C6068] whitespace-nowrap">
            {waktuRelatif(v.detected_at)}
          </span>
        </div>
      ))}
    </div>
  );
}