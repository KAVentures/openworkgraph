// Browser-native cut events complement the existing copy/paste listeners in
// content.js. Only the occurrence and safe target semantics are emitted; the
// selected/clipboard contents are never read.
addEventListener("cut", (e) => send("cut", semanticTarget(e)), true);
