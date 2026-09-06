import { useState } from "react";
import { useVoiceInput } from "../hooks/useVoiceInput.js";

export default function IntentBar({ selection, onSend, sending }) {
  const [text, setText] = useState("");
  const { listening, start, stop } = useVoiceInput((transcript) => setText(transcript));

  function handleSend() {
    if (!text.trim()) return;
    onSend(text.trim());
    setText("");
  }

  return (
    <div style={styles.bar}>
      <div style={styles.selection}>
        {selection
          ? `Selected: ${selection.cad_object_name} @ (${selection.world_position.x.toFixed(1)}, ${selection.world_position.y.toFixed(1)}, ${selection.world_position.z.toFixed(1)}) mm`
          : "Tap a surface to select it"}
      </div>
      <div style={styles.row}>
        <input
          style={styles.input}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={selection ? "e.g. put a 6mm hole here" : "select a surface first"}
          disabled={!selection}
        />
        <button style={styles.iconBtn} onClick={listening ? stop : start} disabled={!selection}>
          {listening ? "..." : "mic"}
        </button>
        <button style={styles.sendBtn} onClick={handleSend} disabled={!selection || !text.trim() || sending}>
          {sending ? "..." : "Send"}
        </button>
      </div>
    </div>
  );
}

const styles = {
  bar: {
    position: "fixed", left: 0, right: 0, bottom: 0,
    padding: "10px 12px calc(10px + env(safe-area-inset-bottom))",
    background: "#1a1a1a", borderTop: "1px solid #333",
    color: "#ddd", fontFamily: "sans-serif",
  },
  selection: { fontSize: 12, marginBottom: 6, color: "#9ad" },
  row: { display: "flex", gap: 8 },
  input: {
    flex: 1, padding: "10px 12px", borderRadius: 8,
    border: "1px solid #444", background: "#222", color: "#fff", fontSize: 14,
  },
  iconBtn: { padding: "0 14px", borderRadius: 8, border: "1px solid #444", background: "#2a2a2a", color: "#fff" },
  sendBtn: { padding: "0 16px", borderRadius: 8, border: "none", background: "#007acc", color: "#fff", fontWeight: "bold" },
};
