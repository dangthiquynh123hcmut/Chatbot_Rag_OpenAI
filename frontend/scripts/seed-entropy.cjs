const crypto = require("crypto");

crypto.randomBytes(32);

const self = __filename.replace(/\\/g, "/");
const current = process.env.NODE_OPTIONS || "";
if (!current.includes(self)) {
  process.env.NODE_OPTIONS = `${current} --require "${self}"`.trim();
}
