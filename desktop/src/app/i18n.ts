/**
 * Interface language — machine-local, like appearance (dsh's `locale`).
 *
 * English is the source of truth: every key's English string lives in the
 * component that uses it (as the i18next defaultValue), so untranslated
 * keys render their English text instead of a key name, and the `en`
 * resource table stays empty by construction. zh-CN carries the first
 * translated batch: the settings dialog, the sidebar, and the composer's
 * high-traffic strings. Coverage grows per key — never a blocker.
 */

import i18n from "i18next";
import { initReactI18next } from "react-i18next";

const STORAGE_KEY = "deepcode.desktop.locale.v1";

export const LOCALES = [
  { value: "en", label: "English" },
  { value: "zh-CN", label: "简体中文" },
] as const;

export type Locale = (typeof LOCALES)[number]["value"];

const ZH_CN: Record<string, string> = {
  // Settings dialog shell
  "settings.title": "设置",
  "settings.section.general": "通用",
  "settings.section.models": "模型",
  "settings.section.plugins": "插件",
  "settings.section.presets": "智能体预设",
  "settings.writeTo": "写入到",
  "settings.scope.user": "用户配置",
  "settings.scope.project": "当前项目",
  "settings.openConfig": "打开配置文件",
  "settings.close": "关闭设置",
  // General rows
  "settings.preset.title": "智能体预设",
  "settings.preset.eyebrow": "会话默认值",
  "settings.preset.label": "新会话默认使用",
  "settings.preset.none": "无 · 默认组合",
  "settings.preset.save": "保存预设默认值",
  "settings.permissions.title": "权限",
  "settings.permissions.eyebrow": "安全策略",
  "settings.permissions.label": "会话默认权限",
  "settings.permissions.save": "保存安全设置",
  "settings.appearance.title": "外观",
  "settings.appearance.eyebrow": "显示",
  "settings.appearance.light": "浅色",
  "settings.appearance.dark": "深色",
  "settings.appearance.system": "跟随系统",
  "settings.language.title": "语言",
  "settings.language.eyebrow": "界面语言",
  "settings.language.label": "界面语言",
  "settings.language.note": "立即生效,仅保存在本机。英文原文是所有文案的事实来源。",
  "settings.composer.title": "忙碌时的 Enter 行为",
  "settings.composer.eyebrow": "输入框",
  "settings.composer.label": "当 Turn 正在运行时,Enter 会…",
  "settings.composer.steer": "引导当前 Turn",
  "settings.composer.queue": "排队为下一个 Turn",
  "settings.composer.note":
    "仅在忙碌时生效;Cmd/Ctrl+Enter 执行另一种行为。空闲时 Enter 始终发送。本设置立即生效,仅保存在本机。",
  // Sidebar
  "sidebar.threads": "会话",
  "sidebar.automations": "自动化",
  "sidebar.skills": "技能",
  "sidebar.plugins": "插件",
  "sidebar.mcp": "MCP",
  "sidebar.settings": "设置",
  "sidebar.projects": "项目",
  // Composer high-traffic strings
  "composer.hint.send": "↵ 发送",
  "composer.hint.steerQueue": "↵ 引导 · ⌘↵ 排队",
  "composer.hint.queueSteer": "↵ 排队 · ⌘↵ 引导",
  "composer.hint.newline": "⇧↵ 换行",
  "composer.queueNext": "排队下一个",
};

function readLocale(): Locale {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && LOCALES.some((locale) => locale.value === stored)) {
      return stored as Locale;
    }
  } catch {
    // Preferences must never block startup.
  }
  return typeof navigator !== "undefined" &&
    navigator.language?.toLowerCase().startsWith("zh")
    ? "zh-CN"
    : "en";
}

export function setLocale(locale: Locale): void {
  try {
    localStorage.setItem(STORAGE_KEY, locale);
  } catch {
    // The session still honours the choice.
  }
  void i18n.changeLanguage(locale);
}

export function initI18n(): typeof i18n {
  if (!i18n.isInitialized) {
    void i18n.use(initReactI18next).init({
      lng: readLocale(),
      fallbackLng: "en",
      resources: {
        en: { translation: {} },
        "zh-CN": { translation: ZH_CN },
      },
      interpolation: { escapeValue: false },
      // English lives inline as defaultValue at each call site.
      returnEmptyString: false,
    });
  }
  return i18n;
}

/** Reset for tests: re-init with a known language. */
export function __setLocaleForTests(locale: Locale): void {
  void i18n.changeLanguage(locale);
}
