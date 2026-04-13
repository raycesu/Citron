import { useState } from "react";

function StatCard({ label, value, thisWeek }) {
  const [hovered, setHovered] = useState(false);

  const baseStyle = {
    background: thisWeek
      ? 'linear-gradient(145deg, #1a1008 0%, #111111 100%)'
      : 'linear-gradient(145deg, #161616 0%, #111111 100%)',
    border: hovered
      ? '1px solid rgba(255,100,0,0.2)'
      : '1px solid rgba(255,255,255,0.07)',
    borderTop: thisWeek ? '2px solid #FF6200' : undefined,
    borderRadius: '16px',
    padding: '20px 22px',
    transition: 'border-color 0.2s ease, transform 0.2s ease',
    cursor: 'default',
    transform: hovered ? 'translateY(-2px)' : 'none',
  };

  return (
    <div
      style={baseStyle}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div
        style={{
          fontFamily: "'JetBrains Mono', 'SF Mono', monospace",
          fontSize: '28px',
          fontWeight: 700,
          color: '#F5F5F5',
          letterSpacing: '-0.03em',
          lineHeight: 1,
        }}
      >
        {value ?? '—'}
      </div>
      <div
        style={{
          fontSize: '11px',
          textTransform: 'uppercase',
          letterSpacing: '0.1em',
          color: 'rgba(255,255,255,0.35)',
          marginTop: '6px',
        }}
      >
        {label}
      </div>
    </div>
  );
}

export default function StatsRow({ stats, loading }) {
  const items = [
    {
      label: 'Total Events',
      value: loading ? '…' : stats?.total_events,
    },
    {
      label: 'Travel Grants',
      value: loading ? '…' : stats?.travel_grants,
    },
    {
      label: 'In Person',
      value: loading ? '…' : stats?.in_person_events,
    },
    {
      label: 'Canada / US',
      value: loading ? '…' : stats?.canada_us_events,
    },
    {
      label: 'This Week',
      value: loading ? '…' : stats?.events_next_7_days,
      thisWeek: true,
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3 w-full">
      {items.map((item) => (
        <StatCard key={item.label} {...item} />
      ))}
    </div>
  );
}
