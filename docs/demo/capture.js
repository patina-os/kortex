// SPDX-License-Identifier: GPL-3.0-or-later
// Turn the recording into the README's animation: fill timeline.json into page.html
// (replay.html, open it in a browser to step through), play it in headless Chromium in
// its #gif mode, record the stage and convert it with ffmpeg into kortex-demo.gif.
// Needs Node's playwright with Chromium and ffmpeg:  node docs/demo/capture.js
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const { chromium } = require("playwright");

const here = __dirname;
const timeline = fs.readFileSync(path.join(here, "timeline.json"), "utf8").replace(/<\//g, "<\\/");
const page = fs.readFileSync(path.join(here, "page.html"), "utf8").replace("__TIMELINE__", timeline);
const replay = path.join(here, "replay.html");
fs.writeFileSync(replay, page);

(async () => {
  const videos = fs.mkdtempSync(path.join(require("os").tmpdir(), "kortex-gif-"));
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 880, height: 500 }, recordVideo: { dir: videos, size: { width: 880, height: 500 } },
  });
  const tab = await context.newPage();
  await tab.goto("file://" + replay + "#gif");
  await tab.waitForSelector("body[data-done]", { timeout: 120000 });
  await tab.waitForTimeout(2500);  // rest on the last frame
  await context.close();
  await browser.close();
  const video = path.join(videos, fs.readdirSync(videos).find((f) => f.endsWith(".webm")));
  const out = path.join(here, "kortex-demo.gif");
  // Two passes: a palette made for these frames, then the GIF at 6 frames a second, 800 pixels wide.
  const filters = "fps=6,scale=800:-1:flags=lanczos";
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-i", video, "-vf", `${filters},palettegen=stats_mode=diff`,
    path.join(videos, "palette.png")]);
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-i", video, "-i", path.join(videos, "palette.png"),
    "-lavfi", `${filters} [x]; [x][1:v] paletteuse=dither=none:diff_mode=rectangle`, out]);
  fs.rmSync(videos, { recursive: true, force: true });
  console.log(`capture: wrote ${out} (${(fs.statSync(out).size / 1e6).toFixed(2)} MB)`);
})();
