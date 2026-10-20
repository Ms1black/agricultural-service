// Персонаж — тот же спрайт 15×15, что на экране устройства (firmware/autopoliv_ui/jarvis_sprite.h).

const SPRITE = ["........aa.....", ".......aa......", ".......a.......", "....bbbabb.....",
  "...cddddddb....", "..cdddddddcb...", ".cdddddddddcc..", "cddddddddddddc.", "cddaefaaefaddc.",
  "cddaffaaffaddc.", "cddgddddddgdddc", "bdddddddddddddc", "bddddddddddddc.", ".bddddddddddcc.",
  "..bbaa..aabc..."];
export const PAL: Record<string, string> = {
  a: "#79ff31", b: "#5bad25", c: "#74cb3a", d: "#76e238", e: "#fff", f: "#000", g: "#fbacd4",
};

/** blink — веки плавно опускаются на глаза; fast — чаще (во время полива). */
export function Jarvis({ blink = false, fast = false, className }: {
  blink?: boolean; fast?: boolean; className?: string;
}) {
  const rects = [];
  for (let j = 0; j < SPRITE.length; j++) {
    for (let i = 0; i < SPRITE[j].length; i++) {
      const ch = SPRITE[j][i];
      if (ch !== ".") rects.push(<rect key={`${i}-${j}`} x={i} y={j} width={1.02} height={1.02} fill={PAL[ch]} />);
    }
  }
  return (
    <svg viewBox="0 0 15 15" shapeRendering="crispEdges" aria-hidden="true" className={className}>
      {rects}
      {blink && [4, 8].map(x => (
        <rect key={x} className={`lid${fast ? " fast" : ""}`} x={x} y={8} width={2} height={2} fill={PAL.d} />
      ))}
    </svg>
  );
}

/** Значок вкладки браузера из спрайта. */
export function setFavicon() {
  const c = document.createElement("canvas");
  c.width = c.height = 30;
  const g = c.getContext("2d");
  if (!g) return;
  SPRITE.forEach((row, j) => [...row].forEach((ch, i) => {
    if (ch !== ".") { g.fillStyle = PAL[ch]; g.fillRect(i * 2, j * 2, 2, 2); }
  }));
  const link = document.getElementById("favicon") as HTMLLinkElement | null;
  if (link) link.href = c.toDataURL();
}
