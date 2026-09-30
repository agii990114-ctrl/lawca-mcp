import { useEffect, useState } from "react";
import { get, qs } from "../api";
import type { Matter } from "../types";

/** 접근 그룹 안의 사건을 검색해서 고르는 콤보박스(사건번호·당사자·사건명으로 검색) */
export function MatterPicker({ group, value, onChange, id }: {
  group: string; value: string; onChange: (matterId: string, matter?: Matter) => void; id?: string;
}) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Matter[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const r = await get<{ items: Matter[] }>(`/matters${qs({ access_group: group, q, limit: 30 })}`);
        if (!cancelled) { setItems(r.items); setError(null); }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "사건을 불러오지 못했습니다.");
      }
    }, 250);
    return () => { cancelled = true; clearTimeout(t); };
  }, [group, q]);

  return (
    <div className="picker">
      <input id={id} type="search" placeholder="사건번호·당사자·사건명으로 검색" value={q} onChange={(e) => setQ(e.target.value)}
        aria-label="사건 검색" />
      <select value={value} onChange={(e) => onChange(e.target.value, items.find((m) => m.matter_id === e.target.value))}
        aria-label="사건 선택" size={Math.min(6, Math.max(3, items.length + 1))}>
        <option value="">사건을 선택하세요…</option>
        {items.map((m) => (
          <option key={m.matter_id} value={m.matter_id}>
            {m.case_no} · {m.case_type} · {m.plaintiff} v. {m.defendant}
          </option>
        ))}
      </select>
      {error && <div className="alert alert-error" role="alert">{error}</div>}
      {!error && items.length === 0 && <div className="hint">이 접근 그룹에 검색되는 사건이 없습니다. 사건 메뉴에서 먼저 등록해 주세요.</div>}
    </div>
  );
}
