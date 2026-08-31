'use client';

import { useEffect, useState } from 'react';

export default function LiveMonitor({ initialCameras }: { initialCameras: string[] }) {
  const [cameras, setCameras] = useState<string[]>(initialCameras);
  const [aktif, setAktif] = useState<string>(initialCameras[0] ?? '');

  useEffect(() => {
    const streamUrl = process.env.NEXT_PUBLIC_STREAM_URL;
    fetch(`${streamUrl}/cameras`)
      .then((r) => r.json())
      .then((data) => {
        if (data.cameras?.length) {
          setCameras(data.cameras);
          setAktif((prev) => (data.cameras.includes(prev) ? prev : data.cameras[0]));
        }
      })
      .catch(() => {});
  }, []);

  return (
    <div className="rounded-lg overflow-hidden border border-[#2A2F38] bg-[#1C2128]">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-[#2A2F38] gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#E5484D] opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-[#E5484D]" />
          </span>
          <span className="text-xs font-[family-name:var(--font-mono)] tracking-wide text-[#E5484D]">LANGSUNG</span>
        </div>

        <select
          value={aktif}
          onChange={(e) => setAktif(e.target.value)}
          className="bg-[#14171C] border border-[#2A2F38] text-sm rounded px-3 py-1.5 outline-none focus:border-[#8A8F98]"
        >
          {cameras.map((nama) => (
            <option key={nama} value={nama}>{nama}</option>
          ))}
        </select>
      </div>

      {aktif && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          key={aktif}
          src={`${process.env.NEXT_PUBLIC_STREAM_URL}/stream/${encodeURIComponent(aktif)}`}
          alt={`Tayangan langsung kamera ${aktif}`}
          className="w-full block"
        />
      )}
    </div>
  );
}