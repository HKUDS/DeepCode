import type { Page } from "@playwright/test";

/**
 * CSS rules of one component that style another component's elements.
 *
 * A module rule such as `.context strong` reaches every <strong> inside its
 * element, including those of components rendered there; that is how a
 * toolbar once restyled the model picker inside it. Built class names are
 * `_<name>_<filehash>_<line>`, so the file hash says which module a class
 * belongs to: a rule leaks when an element it matches sits inside another
 * module's element below the rule's own. Returns one line per leaking rule.
 */
export function crossComponentRules(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const moduleOf = (name: string) =>
      /^_[A-Za-z0-9]+_([a-z0-9]{5})_\d+$/.exec(name)?.[1] ?? null;
    const leaks = new Set<string>();
    const visit = (rules: CSSRuleList) => {
      for (const rule of Array.from(rules)) {
        if (rule instanceof CSSGroupingRule) {
          visit(rule.cssRules);
          continue;
        }
        if (!(rule instanceof CSSStyleRule)) continue;
        for (const selector of rule.selectorText.split(",").map((part) => part.trim())) {
          // A module class, a descendant combinator, ending in an element.
          const match =
            /\.(_[A-Za-z0-9]+_([a-z0-9]{5})_\d+)[^\s>+~]*\s+(?:[^\s>+~]+\s+)*[a-z][a-z0-9]*(?:[:[][^\s]*)?$/.exec(
              selector,
            );
          if (!match) continue;
          const [, anchorClass, ruleModule] = match;
          let matched: NodeListOf<Element>;
          try {
            matched = document.querySelectorAll(selector);
          } catch {
            continue;
          }
          for (const element of Array.from(matched)) {
            const anchor = element.closest(`.${anchorClass}`);
            for (let node: Element | null = element; node && node !== anchor; node = node.parentElement) {
              const owner = Array.from(node.classList).map(moduleOf).find(Boolean);
              if (owner && owner !== ruleModule) {
                leaks.add(`${selector} styles <${element.tagName.toLowerCase()}> of module ${owner}`);
              }
              if (owner) break;
            }
          }
        }
      }
    };
    for (const sheet of Array.from(document.styleSheets)) {
      try {
        visit(sheet.cssRules);
      } catch {
        // A stylesheet from another origin cannot be read; none are ours.
      }
    }
    return [...leaks];
  });
}
