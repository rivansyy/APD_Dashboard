'use client';

import { useEffect, useState } from 'react';
import { io } from 'socket.io-client';

type StatRow = { status: string; jumlah: number };

const cocokkan = (rows: StatRow[], kandidat: string[]) =>
  rows.filter((r) => kandidat.includes(r.status)).reduce((total, r) => total + r.jumlah, 0);

export default function MetricsPanel({ initialStats }: { initialStats: StatRow[] }) {
  const [stats, setStats] = useState<StatRow[]>(initialStats);

  const muat = () => {
    fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/events/stats`)
      .then((r) => r.json())
      .then(setStats)
      .catch(() => {});
  };

  useEffect(() => {
    const socket = io(process.env.NEXT_PUBLIC_API_URL);
    socket.on('new_violation', muat); // tiap ada insiden baru, ambil ulang angka terbaru
    return () => { socket.disconnect(); };
  }, []);

  const tidakLengkap = cocokkan(stats, ['APD TIDAK LENGKAP']);
  const tanpaApd = cocokkan(stats, ['TIDAK MENGGUNAKAN APD', 'NO APD']);

  return (
    <div className="flex flex-col gap-3">
      <Kartu label="Lengkap" nilai="—" warna="#3DD68C" catatan="belum dilacak" />
      <Kartu label="Tidak Lengkap" nilai={tidakLengkap} warna="#F5B700" />
      <Kartu label="Tanpa APD" nilai={tanpaApd} warna="#E5484D" />
    </div>
  );
}

function Kartu({ label, nilai, warna, catatan }: { label: string; nilai: number | string; warna: string; catatan?: string }) {
  return (
    <div className="flex-1 rounded-lg border border-[#2A2F38] bg-[#1C2128] px-5 py-4">
      <div className="flex items-center gap-2 mb-1">
        <span className="w-2 h-2 rounded-full" style={{ backgroundColor: warna }} />
        <span className="text-sm text-[#B8BCC4]">{label}</span>
        {catatan && <span className="text-[10px] text-[#5C6068]">({catatan})</span>}
      </div>
      <span className="text-3xl font-semibold font-[family-name:var(--font-mono)]" style={{ color: warna }}>
        {nilai}
      </span>
    </div>
  );
}