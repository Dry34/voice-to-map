// Langue (FR / EN) et thème (clair / sombre), partagés par l'accueil et la carte.
// Les choix sont gardés dans le navigateur ; par défaut, la langue est le français
// et le thème suit le réglage du système.

const LANG_KEY = "vtm-lang";
const THEME_KEY = "vtm-theme";

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* navigation privée */ } },
};

const FLAGS = {
  // Drapeau de la langue vers laquelle on bascule.
  en: `<svg viewBox="0 0 60 30" aria-hidden="true"><clipPath id="uk-c"><path d="M0 0v30h60V0z"/></clipPath>
    <clipPath id="uk-t"><path d="M30 15h30v15zv15H0zH0V0zV0h30z"/></clipPath><g clip-path="url(#uk-c)">
    <path d="M0 0v30h60V0z" fill="#012169"/><path d="M0 0l60 30m0-30L0 30" stroke="#fff" stroke-width="6"/>
    <path d="M0 0l60 30m0-30L0 30" clip-path="url(#uk-t)" stroke="#C8102E" stroke-width="4"/>
    <path d="M30 0v30M0 15h60" stroke="#fff" stroke-width="10"/><path d="M30 0v30M0 15h60" stroke="#C8102E" stroke-width="6"/></g></svg>`,
  fr: `<svg viewBox="0 0 3 2" aria-hidden="true"><path fill="#002654" d="M0 0h1v2H0z"/><path fill="#fff" d="M1 0h1v2H1z"/>
    <path fill="#CE1126" d="M2 0h1v2H2z"/></svg>`,
};
const SUN = `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.5" fill="currentColor"/><g stroke="currentColor"
  stroke-width="2" stroke-linecap="round"><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/></g></svg>`;
const MOON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M20.5 14.6A8.5 8.5 0 0 1 9.4 3.5a8.5 8.5 0 1 0 11.1 11.1z"/></svg>`;

// Lien partageable : ?lang=en, ?theme=dark (le choix est ensuite mémorisé).
const params = new URLSearchParams(location.search);
if (["fr", "en"].includes(params.get("lang"))) store.set(LANG_KEY, params.get("lang"));
if (["light", "dark"].includes(params.get("theme"))) store.set(THEME_KEY, params.get("theme"));

let dict = {};
let lang = store.get(LANG_KEY) === "en" ? "en" : "fr";
const listeners = [];

export function currentLang() { return lang; }

/** Texte traduit pour une clé ({fr, en} dans le dictionnaire de la page). */
export function t(key) {
  const entry = dict[key];
  return entry ? entry[lang] ?? entry.fr : key;
}

/** Appelé à chaque changement de langue (pour les textes construits en JS). */
export function onLangChange(fn) { listeners.push(fn); }

function applyLang() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-html]").forEach((el) => { el.innerHTML = t(el.dataset.i18nHtml); });
  document.querySelectorAll("[data-i18n-attr]").forEach((el) => {
    for (const pair of el.dataset.i18nAttr.split(";")) {
      const [attr, key] = pair.split(":");
      el.setAttribute(attr, t(key));
    }
  });
  if (dict["meta.title"]) document.title = t("meta.title");
  const desc = document.querySelector('meta[name="description"]');
  if (desc && dict["meta.description"]) desc.content = t("meta.description");
  const btn = document.getElementById("lang-toggle");
  if (btn) {
    const other = lang === "fr" ? "en" : "fr";
    btn.innerHTML = FLAGS[other];
    btn.setAttribute("aria-label", other === "en" ? "Switch to English" : "Passer en français");
    btn.title = btn.getAttribute("aria-label");
  }
  listeners.forEach((fn) => fn(lang));
}

function systemTheme() {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const btn = document.getElementById("theme-toggle");
  if (btn) {
    const dark = theme === "dark";
    btn.innerHTML = dark ? SUN : MOON;
    const label = dark
      ? (lang === "en" ? "Switch to light mode" : "Passer en mode clair")
      : (lang === "en" ? "Switch to dark mode" : "Passer en mode sombre");
    btn.setAttribute("aria-label", label);
    btn.title = label;
  }
}

/** Branche les boutons drapeau et thème, et traduit la page. */
export function setupPrefs(pageDict) {
  dict = pageDict;
  applyTheme(store.get(THEME_KEY) || systemTheme());
  applyLang();
  document.getElementById("lang-toggle")?.addEventListener("click", () => {
    lang = lang === "fr" ? "en" : "fr";
    store.set(LANG_KEY, lang);
    applyLang();
    applyTheme(document.documentElement.dataset.theme);
  });
  document.getElementById("theme-toggle")?.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    store.set(THEME_KEY, next);
    applyTheme(next);
  });
}
