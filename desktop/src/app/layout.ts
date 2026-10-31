/**
 * The two widths where the layout changes, written once. App marks the shell
 * with data attributes for them (data-layout, data-review) and the styles key
 * off those attributes rather than repeating the widths in media queries.
 */

/** Below this the sidebar is a drawer over the conversation. */
export const NARROW_LAYOUT = "(max-width: 720px)";

/** Below this the review panel floats over the conversation. */
export const FLOATING_REVIEW = "(max-width: 1180px)";
