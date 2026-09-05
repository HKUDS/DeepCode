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
  "settings.appearance.mode": "外观模式",
  "settings.appearance.theme": "主题",
  "settings.appearance.conversationWidth": "对话宽度",
  "settings.appearance.fontSize": "字号",
  "settings.appearance.fontFamily": "首选字体",
  "settings.appearance.paper": "纸张 · 暖色低蓝光",
  "settings.appearance.midnight": "午夜 · 深邃冷色",
  "settings.appearance.claude": "Claude · 象牙白与陶土色",
  "settings.appearance.claudeDark": "Claude 深色 · 石板灰与陶土色",
  "settings.appearance.contrast": "高对比度 · AAA",
  "settings.appearance.fontPlaceholder": "例如：更纱黑体 SC、Inter",
  "settings.appearance.addInstalledFont": "添加已安装字体…",
  "settings.appearance.fontGroup.interface": "界面字体",
  "settings.appearance.fontGroup.monospace": "等宽字体",
  "settings.appearance.fontGroup.cjk": "中日韩字体",
  "settings.appearance.fontDescription":
    "以逗号分隔的字体会优先于内置字体尝试，便于为中英文混排选择一致的字体。系统会跳过未安装的字体，因此可以安全地列出多个字体。",
  "settings.appearance.localOnly":
    "这些显示设置仅保存在本机，会立即生效，不属于项目配置。",
  "settings.appearance.reset": "恢复默认设置",
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
  // Updates card
  "settings.updates.eyebrow": "签名发布通道",
  "settings.updates.title": "应用更新",
  "settings.updates.check": "检查更新",
  "settings.updates.checking": "正在检查…",
  "settings.updates.install": "安装",
  "settings.updates.preparing": "准备中…",
  "settings.updates.downloading": "下载中…",
  "settings.updates.installing": "安装中…",
  "settings.updates.upToDate": "当前已是最新版本。",
  "settings.updates.available":
    "DeepCode {{version}} 可用。安装前会验证软件包签名。",
  "settings.updates.idle":
    "仅在手动请求时检查更新。开发版本可能未配置发布通道。",
  "settings.updates.checkingNote": "正在检查签名发布通道。",
  // Diagnostics card
  "settings.diagnostics.eyebrow": "故障排除",
  "settings.diagnostics.title": "诊断",
  "settings.diagnostics.export": "导出报告",
  "settings.diagnostics.exporting": "导出中…",
  "settings.diagnostics.runChecks": "运行检查",
  "settings.diagnostics.savedTo": "已保存脱敏诊断报告至 {{path}}",
  "settings.diagnostics.noProject": "未选择项目",
  // Sidebar
  "sidebar.threads": "会话",
  "sidebar.automations": "自动化",
  "sidebar.skills": "技能",
  "sidebar.plugins": "插件",
  "sidebar.mcp": "MCP",
  "sidebar.settings": "设置",
  "sidebar.projects": "项目",
  "sidebar.newThread": "新建会话",
  "sidebar.searchSessions": "搜索会话",
  "sidebar.openFolder": "打开本地文件夹",
  "sidebar.openFolderHint": "CLI 中的会话将在此显示。",
  "sidebar.noResults": "无匹配会话",
  "sidebar.noResultsHint": "可按标题、项目或工作空间路径搜索。",
  "sidebar.noSessions": "暂无会话",
  "sidebar.showMore": "显示更多 {{count}} 个",
  "sidebar.showLess": "收起",
  "sidebar.previousSessions": "历史会话",
  "sidebar.folderUnavailable": "原始文件夹不可用",
  "sidebar.localAgent": "本地智能体",
  "sidebar.agentReady": "本地智能体就绪",
  "sidebar.sharedHistory": "共享会话历史",
  // Composer high-traffic strings
  "composer.hint.send": "↵ 发送",
  "composer.hint.steerQueue": "↵ 引导 · ⌘↵ 排队",
  "composer.hint.queueSteer": "↵ 排队 · ⌘↵ 引导",
  "composer.hint.newline": "⇧↵ 换行",
  "composer.queueNext": "排队下一个",
  // Thread header
  "thread.startThread": "开始本地编码会话",
  "thread.folderUnavailable": "文件夹不可用",
  "thread.noLocalFolder": "无本地文件夹",
  "thread.previousSessions": "历史会话",
  "thread.trustFolder": "信任文件夹",
  "thread.trusted": "已信任",
  "thread.trustedTooltip": "允许在此文件夹中执行",
  "thread.folderUnavailableTooltip": "原始会话文件夹不再可用",
  "thread.paper": "论文",
  "thread.paperTooltip": "创建 Paper2Code 会话",
  "thread.fork": "分叉",
  "thread.forkTooltip": "分叉到独立工作树",
  "thread.review": "审查",
  "thread.closeReview": "关闭审查面板",
  "thread.openReview": "打开审查面板",
  // Approval card
  "approval.label": "需要审批",
  "approval.decision": "决定: {{status}}",
  "approval.allowContinue": "允许 {{tool}} 继续?",
  "approval.reviewOperation": "在智能体继续之前,请审查此操作。",
  "approval.sensitiveOperation": "敏感操作",
  "approval.reviewArguments": "查看参数",
  "approval.allowOnce": "允许一次",
  "approval.allowSession": "允许本次会话",
  "approval.deny": "拒绝",
  // Runtime notice
  "runtime.offline": "本地应用服务器不可用。",
  "runtime.restart": "重启服务",
  // Goal rail
  "goal.setGoal": "设定目标",
  "goal.setGoalHint": "在普通 Turn 之间保持一个持久的目标",
  "goal.sessionGoal": "会话目标",
  "goal.turn": "Turn",
  "goal.turns": "Turns",
  "goal.tokens": "tokens",
  "goal.continue": "继续",
  "goal.editGoal": "编辑目标",
  "goal.pause": "暂停",
  "goal.pauseTooltip": "暂停自动继续;不会中断当前 Turn",
  "goal.editReopen": "编辑并重新开启",
  "goal.resume": "恢复",
  "goal.edit": "编辑",
  "goal.newGoal": "新建目标",
  "goal.objectiveHint":
    "目标编辑保持同一 Goal 身份,并到达活跃的 Turn。",
  "goal.outcome": "目标结果",
  "goal.tokenBudget": "Token 预算",
  "goal.tokenBudgetOptional": "可选",
  "goal.noLimit": "无限制",
  "goal.skills": "技能",
  "goal.cancel": "取消",
  "goal.saveResume": "保存并恢复",
  "goal.saveGoal": "保存目标",
  "goal.startGoal": "开始目标",
  "goal.clearGoal": "清除目标",
  "goal.clearConfirm": "确定清除此会话目标?",
  "goal.completionOutcome": "完成结果",
  "goal.blockedOutcome": "阻塞结果",
  "goal.relatedActivity": "相关活动",
  "goal.noSkills": "未选择目标相关技能。",
  "goal.activeTime": "{{duration}} 活跃时间",
  "goal.noUsage": "尚无已完成的 Goal Turn 用量",
  "goal.tokenBudgetLabel": "{{budget}} token 预算",
  "goal.noTokenBudget": "无 token 预算",
  "goal.decidingTurn": "决定 Turn",
  "goal.goalDescription":
    "目标 {{id}} 附属于此会话。普通后续对话引导工作方向,而不重写目标。",
  "goal.closeEditor": "关闭目标编辑器",
  "goal.describeOutcome": "描述 DeepCode 应达成的完整目标。",
  // Inspector
  "inspector.label": "检查器",
  "inspector.views": "检查器视图",
  "inspector.closeReview": "关闭审查面板",
  "inspector.tab.changes": "变更",
  "inspector.tab.files": "文件",
  "inspector.tab.artifacts": "产物",
  "inspector.tab.tests": "测试",
  "inspector.tab.terminal": "终端",
  "inspector.tab.details": "详情",
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
