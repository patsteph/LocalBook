// Friendly model display names — frontend mirror of backend utils/model_display.py,
// for views that only have a raw model id (eval history) and no backend-provided
// display string. Keep in sync with the Python helper (user: friendly names EVERYWHERE).

const capWord = (w: string) => (/^[a-z]?\d/.test(w) ? w : w.charAt(0).toUpperCase() + w.slice(1));
const prettify = (t: string) =>
  t.replace(/[-_]/g, ' ').split(/\s+/).filter(Boolean).map(capWord).join(' ');

export function friendlyModelName(id?: string): string {
  if (!id) return '—';
  // MLX / HuggingFace path form
  if (id.includes('/')) {
    let base = id.split('/').pop() || id;
    base = base.replace(/-(4bit|8bit|bf16|fp16|q4|q8|q4_k_m|q8_0)$/i, '');
    base = base.replace(/-(it|instruct|chat)$/i, '');
    return `${prettify(base)} (MLX)`;
  }
  // Ollama "family:tag"
  const [base, tag] = id.split(':');
  let name = prettify(base);
  if (tag && tag !== 'latest') name += ` ${tag}`;
  return name;
}
