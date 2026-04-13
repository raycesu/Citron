import { useState } from "react";

const TAG_PILLS = [
  { label: "All", value: null },
  { label: "Hackathon", value: "hackathon" },
  { label: "Conference", value: "conference" },
  { label: "Workshop", value: "workshop" },
  { label: "Solana", value: "Solana" },
  { label: "Ethereum", value: "Ethereum" },
  { label: "DeFi", value: "DeFi" },
  { label: "AI", value: "AI" },
];

const REGION_OPTIONS = [
  { label: "All Regions", value: null, province: null },
  { label: "Canada", value: "Canada", province: null },
  { label: "USA", value: "USA", province: null },
  { label: "Ontario Only", value: "Canada", province: "Ontario" },
  { label: "Online", value: "Online", province: null },
];

const SORT_OPTIONS = [
  { label: "Priority", value: "priority" },
  { label: "Soonest", value: "soonest" },
  { label: "Latest Added", value: "latest_added" },
  { label: "Highest Relevance", value: "relevance" },
];

function Toggle({ label, checked, onChange }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        cursor: 'pointer',
        background: 'none',
        border: 'none',
        padding: 0,
      }}
    >
      <div
        style={{
          position: 'relative',
          display: 'inline-flex',
          flexShrink: 0,
          width: '36px',
          height: '20px',
          borderRadius: '999px',
          background: checked
            ? 'linear-gradient(90deg, #FF6200, #FF3D00)'
            : '#222',
          border: checked ? 'none' : '1px solid rgba(255,255,255,0.1)',
          transition: 'background 0.2s ease',
        }}
      >
        <span
          style={{
            position: 'absolute',
            top: checked ? '2px' : '2px',
            left: checked ? 'calc(100% - 18px)' : '2px',
            width: '16px',
            height: '16px',
            borderRadius: '50%',
            background: '#fff',
            boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
            transition: 'left 0.2s ease',
          }}
        />
      </div>
      <span
        style={{
          fontSize: '13px',
          color: 'rgba(255,255,255,0.4)',
          userSelect: 'none',
        }}
      >
        {label}
      </span>
    </button>
  );
}

function PillButton({ label, active, onClick }) {
  const [hovered, setHovered] = useState(false);

  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: active ? 'rgba(255,98,0,0.15)' : '#151515',
        border: active
          ? '1px solid rgba(255,98,0,0.6)'
          : hovered
            ? '1px solid rgba(255,255,255,0.18)'
            : '1px solid rgba(255,255,255,0.08)',
        color: active
          ? '#FF8040'
          : hovered
            ? 'rgba(255,255,255,0.75)'
            : 'rgba(255,255,255,0.45)',
        borderRadius: '999px',
        padding: '6px 16px',
        fontSize: '13px',
        fontWeight: active ? 500 : 400,
        cursor: 'pointer',
        transition: 'all 0.15s ease',
        whiteSpace: 'nowrap',
      }}
    >
      {label}
    </button>
  );
}

export default function FilterBar({ filters, onChange }) {
  function setTag(tag) {
    onChange({ ...filters, tag });
  }

  function setRegion(value) {
    const opt = REGION_OPTIONS.find((o) => o.label === value) || REGION_OPTIONS[0];
    onChange({
      ...filters,
      country: opt.value,
      province_state: opt.province,
    });
  }

  function getRegionLabel() {
    const opt = REGION_OPTIONS.find(
      (o) => o.value === filters.country && o.province === filters.province_state
    );
    return opt ? opt.label : "All Regions";
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '14px',
      }}
    >
      {/* Tag pills */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px' }}>
        {TAG_PILLS.map((pill) => (
          <PillButton
            key={pill.label}
            label={pill.label}
            active={filters.tag === pill.value}
            onClick={() => setTag(pill.value)}
          />
        ))}
      </div>

      {/* Toggles + dropdowns */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '24px' }}>
        <Toggle
          label="In Person Only"
          checked={!!filters.inperson}
          onChange={(v) => onChange({ ...filters, inperson: v })}
        />
        <Toggle
          label="Travel Grant Only"
          checked={!!filters.travel_grant}
          onChange={(v) => onChange({ ...filters, travel_grant: v })}
        />

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginLeft: 'auto', flexWrap: 'wrap' }}>
          <select
            value={getRegionLabel()}
            onChange={(e) => setRegion(e.target.value)}
            className="select-dark"
          >
            {REGION_OPTIONS.map((o) => (
              <option key={o.label} value={o.label}>
                {o.label}
              </option>
            ))}
          </select>

          <select
            value={filters.sort || "priority"}
            onChange={(e) => onChange({ ...filters, sort: e.target.value })}
            className="select-dark"
          >
            {SORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
      </div>
    </div>
  );
}
