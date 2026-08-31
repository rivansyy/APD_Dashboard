import LiveMonitor from './LiveMonitor';
import MetricsPanel from './MetricsPanel';
import ViolationsTable from './ViolationsTable';

type Violation = {
  id: number; camera_name: string; person_id: number;
  status: string; snapshot_path: string; detected_at: string; created_at: string;
};
type StatRow = { status: string; jumlah: number };

async function getViolations(): Promise<Violation[]> {
  const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/events`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Gagal mengambil data pelanggaran');
  return res.json();
}

async function getStats(): Promise<StatRow[]> {
  const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/events/stats`, { cache: 'no-store' });
  if (!res.ok) return [];
  return res.json();
}

async function getCameras(): Promise<string[]> {
  try {
    const res = await fetch(`${process.env.NEXT_PUBLIC_STREAM_URL}/cameras`, { cache: 'no-store' });
    const data = await res.json();
    return data.cameras ?? [];
  } catch {
    return [];
  }
}

export default async function DashboardPage() {
  const [violations, stats, cameras] = await Promise.all([getViolations(), getStats(), getCameras()]);

  return (
    <div className="flex min-h-screen">
      <aside className="w-56 border-r border-[#2A2F38] px-5 py-6 hidden md:flex flex-col gap-6">
        <div>
          <p className="text-xs tracking-[0.2em] text-[#8A8F98] font-[family-name:var(--font-mono)]">K3 MONITOR</p>
          <p className="text-sm text-[#B8BCC4] mt-1">Kepatuhan APD</p>
        </div>
        <nav className="flex flex-col gap-1 text-sm">
          <a href="#live" className="px-3 py-2 rounded bg-[#1C2128] text-[#E8E6E1]">Pemantauan Langsung</a>
          <a href="#insiden" className="px-3 py-2 rounded text-[#8A8F98] hover:text-[#E8E6E1] hover:bg-[#1C2128] transition-colors">Catatan Insiden</a>
        </nav>
      </aside>

      <main className="flex-1 px-6 py-8 md:px-10 md:py-10">
        <header id="live" className="mb-8">
          <p className="text-xs tracking-[0.2em] text-[#8A8F98] font-[family-name:var(--font-mono)] mb-1">
            RUANG PEMANTAUAN K3
          </p>
          <h1 className="text-2xl md:text-3xl font-semibold">Kepatuhan APD — Lantai Produksi</h1>
        </header>

        <section className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-6 mb-10">
          <LiveMonitor initialCameras={cameras} />
          <MetricsPanel initialStats={stats} />
        </section>

        <div
          className="h-2.5 rounded-sm mb-6"
          style={{ backgroundImage: 'repeating-linear-gradient(135deg, #F5B700 0 14px, #14171C 14px 28px)' }}
        />

        <h2 id="insiden" className="text-sm tracking-[0.15em] text-[#8A8F98] font-[family-name:var(--font-mono)] mb-4">
          CATATAN INSIDEN
        </h2>

        <ViolationsTable initialViolations={violations} />
      </main>
    </div>
  );
}