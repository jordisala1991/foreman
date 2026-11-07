// Reordering a list by its grip: with the pointer (a touch too: the grip has touch-action none) or with the arrow
// keys. A row carries `data-sid`; its list, `data-sortable`. `move(id, targetId)` puts `id` where `targetId` is.
import { useState, type KeyboardEvent, type PointerEvent } from "react";
import { t } from "../lib";

export interface Drag {
  id: string;
  over: string | null;
}

export function useSortable(move: (id: string, target: string) => void) {
  const [drag, setDrag] = useState<Drag | null>(null);
  const rowAt = (list: Element, y: number): string | null => {
    const rows = Array.from(list.querySelectorAll<HTMLElement>("[data-sid]"));
    const hit = rows.find((r) => {
      const b = r.getBoundingClientRect();
      return y >= b.top && y <= b.bottom;
    });
    return hit?.dataset.sid ?? null;
  };
  const grip = (id: string, ids: string[]) => ({
    onPointerDown: (e: PointerEvent<HTMLElement>) => {
      e.currentTarget.setPointerCapture(e.pointerId);
      setDrag({ id, over: id });
    },
    onPointerMove: (e: PointerEvent<HTMLElement>) => {
      const list = e.currentTarget.closest("[data-sortable]");
      if (!drag || drag.id !== id || !list) return;
      const over = rowAt(list, e.clientY) ?? drag.over;
      if (over !== drag.over) setDrag({ id, over });
    },
    onPointerUp: () => {
      if (drag && drag.id === id && drag.over && drag.over !== id) move(id, drag.over);
      setDrag(null);
    },
    onPointerCancel: () => setDrag(null),
    onKeyDown: (e: KeyboardEvent<HTMLElement>) => {
      const k = ids.indexOf(id);
      const to = e.key === "ArrowUp" ? ids[k - 1] : e.key === "ArrowDown" ? ids[k + 1] : undefined;
      if (to) {
        e.preventDefault();
        move(id, to);
      }
    },
    "aria-label": t("drag"),
    title: t("drag_hint"),
    type: "button" as const,
    className: "grip",
  });
  const rowClass = (id: string) =>
    drag ? (drag.id === id ? "dragging" : drag.over === id ? "dropping" : "") : "";
  return { grip, rowClass, drag };
}
