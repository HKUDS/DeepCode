/**
 * deepcode-lineguard.js
 * Monkey-patch Node.js readline.createInterface to prevent RangeError
 * from oversized single-line stdout (e.g. MCP tool returning 100MB+ JSON).
 *
 * Usage: NODE_OPTIONS="--require /path/to/deepcode-lineguard.js" deepcode
 */

'use strict';

const { Transform } = require('stream');
const _readline = require('readline');

// 10MB max per line — well below V8's ~268MB string limit
const MAX_LINE_LENGTH = 10 * 1024 * 1024;

const _origCreateInterface = _readline.createInterface;

_readline.createInterface = function (opts) {
  if (opts && opts.input && !opts._lineguardInstalled) {
    const originalInput = opts.input;
    let pending = '';

    const lineGuard = new Transform({
      decodeStrings: false,
      encoding: 'utf8',

      transform(chunk, _encoding, callback) {
        // Convert to string (handles both Buffer and string chunks)
        const str = typeof chunk === 'string' ? chunk : chunk.toString('utf8');

        // Emit safe-chunked output: split on newlines, cap each segment
        const parts = (pending + str).split('\n');
        // The last part might be incomplete — hold it for next chunk
        pending = parts.pop();

        for (let i = 0; i < parts.length; i++) {
          let segment = parts[i];
          if (segment.length > MAX_LINE_LENGTH) {
            const mb = (segment.length / 1024 / 1024).toFixed(1);
            segment = JSON.stringify({
              error: `[LINE_TRUNCATED] ${mb}MB single-line response was truncated to prevent Node.js readline overflow.`,
              original_size: segment.length,
              truncated: true,
            });
          }
          this.push(segment);
          if (i < parts.length - 1) {
            this.push('\n');
          } else {
            // Last part gets a newline (we already split on \n so add it back)
            this.push('\n');
          }
        }

        // Flush any remaining pending if it's getting too large
        if (pending.length > MAX_LINE_LENGTH) {
          const mb = (pending.length / 1024 / 1024).toFixed(1);
          this.push(JSON.stringify({
            error: `[LINE_TRUNCATED] ${mb}MB single-line response was truncated to prevent Node.js readline overflow.`,
            original_size: pending.length,
            truncated: true,
          }));
          this.push('\n');
          pending = '';
        }

        callback();
      },

      flush(callback) {
        if (pending.length > 0) {
          if (pending.length > MAX_LINE_LENGTH) {
            const mb = (pending.length / 1024 / 1024).toFixed(1);
            this.push(JSON.stringify({
              error: `[LINE_TRUNCATED] ${mb}MB single-line response was truncated.`,
              original_size: pending.length,
              truncated: true,
            }));
          } else {
            this.push(pending);
          }
          this.push('\n');
        }
        callback();
      },
    });

    // Tag to prevent double-wrapping if createInterface is called multiple times
    opts._lineguardInstalled = true;
    opts.input = originalInput.pipe(lineGuard);
  }

  return _origCreateInterface.call(this, opts);
};

// Also patch the maxListeners to avoid warnings for many MCP servers
process.setMaxListeners(50);
