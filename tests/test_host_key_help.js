"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const context = {};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf("function sshHostKeyHelp("), source.indexOf("function nodeCard(")), context);

const unknown = "No ED25519 host key is known for 127.0.0.1 and you have requested strict checking.\nHost key verification failed.";
let help = context.sshHostKeyHelp(unknown, {host: "synthetic-alias", user: "synthetic-user", port: 2222,
  auth_type: "password", password: "synthetic-secret"});
assert.match(help.summary, /尚未登记/);
assert.match(help.instruction, /同一账号/);
assert.equal(help.command, "ssh -o StrictHostKeyChecking=ask -p 2222 -l synthetic-user synthetic-alias");
assert.equal(JSON.stringify(help).includes("synthetic-secret"), false);
assert.equal(context.sshHostKeyHelp(unknown).command, "ssh -o StrictHostKeyChecking=ask -p 22 -l root 节点地址");
help = context.sshHostKeyHelp("WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!\nHost key verification failed.");
assert.match(help.summary, /不一致/);
assert.equal(help.command, undefined);
assert.match(context.sshHostKeyHelp("Host key verification failed.").summary, /验证失败/);
assert.equal(context.sshHostKeyHelp("Permission denied (password)."), null);
assert.equal(context.sshHostKeyHelp("Connection timed out"), null);
console.log("SSH host-key guidance checks passed: unknown, changed, generic failure and authentication errors.");
