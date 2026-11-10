// Minimal i18n: JSON message catalogs in /static/locales/<locale>.json, `{name}` interpolation,
// CLDR plurals via Intl.PluralRules ({one, other, ...} objects selected by `count`, optional `zero`),
// and declarative DOM translation through data-i18n* attributes.
const i18n = (() => {
  const LOCALES = { en: "English", "pt-BR": "Português (Brasil)" };
  const FALLBACK = "en";
  const STORAGE_KEY = "locale";

  let locale = FALLBACK;
  let messages = {};
  let fallback = {};

  function detect() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved in LOCALES) return saved;
    } catch { /* storage unavailable */ }
    for (const lang of navigator.languages || [navigator.language]) {
      if (lang in LOCALES) return lang;
      const base = lang.split("-")[0];
      const match = Object.keys(LOCALES).find((l) => l.split("-")[0] === base);
      if (match) return match;
    }
    return FALLBACK;
  }

  async function load(loc) {
    const res = await fetch(`/static/locales/${loc}.json`);
    if (!res.ok) throw new Error(`locale ${loc}: ${res.status}`);
    return res.json();
  }

  const lookup = (catalog, key) => key.split(".").reduce((node, part) => (node == null ? node : node[part]), catalog);

  function format(value) {
    return typeof value === "number" ? value.toLocaleString(locale) : String(value);
  }

  function t(key, params = {}) {
    let msg = lookup(messages, key) ?? lookup(fallback, key);
    if (msg == null) return key;
    if (typeof msg === "object") {
      const n = params.count ?? 0;
      msg = (n === 0 && msg.zero) || msg[new Intl.PluralRules(locale).select(n)] || msg.other;
    }
    return msg.replace(/\{(\w+)\}/g, (m, name) => (name in params ? format(params[name]) : m));
  }

  // data-i18n -> textContent; data-i18n-<attr> -> that attribute (title, placeholder, aria-label…)
  function translate(root = document) {
    for (const el of root.querySelectorAll("*")) {
      for (const { name, value } of [...el.attributes]) {
        if (name === "data-i18n") el.textContent = t(value);
        else if (name.startsWith("data-i18n-")) el.setAttribute(name.slice(10), t(value));
      }
    }
  }

  async function setLocale(loc) {
    if (!(loc in LOCALES)) loc = FALLBACK;
    messages = loc === FALLBACK ? fallback : await load(loc);
    locale = loc;
    document.documentElement.lang = loc;
    try { localStorage.setItem(STORAGE_KEY, loc); } catch { /* storage unavailable */ }
    translate();
    document.dispatchEvent(new CustomEvent("localechange", { detail: { locale } }));
  }

  async function init() {
    fallback = await load(FALLBACK);
    const loc = detect();
    if (loc === FALLBACK) {
      messages = fallback;
      document.documentElement.lang = loc;
      translate();
    } else {
      await setLocale(loc).catch(() => setLocale(FALLBACK));
    }
  }

  return {
    init,
    t,
    translate,
    setLocale,
    get locale() { return locale; },
    locales: LOCALES,
    number: (n, opts) => n.toLocaleString(locale, opts),
    date: (d, opts) => d.toLocaleString(locale, opts),
  };
})();
const t = i18n.t;
