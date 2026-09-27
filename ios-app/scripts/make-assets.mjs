// Draws the app icon and the launch (splash) image from the Pomona mark.
//
// The mark is the same four strokes as static/login.html and
// static/shell.js's MARK_PATHS; the colors are static/theme.css's --spruce
// and --apricot. static/icons/icon-512.png (the website's icon) is the
// look being matched: apricot mark, centred, on a full spruce square. It's
// redrawn from the vector rather than scaled up because the App Store needs
// a sharp 1024 x 1024.
//
// Run `npm run assets` after changing the mark or the colors, then commit
// the PNGs it writes under ios/App/App/Assets.xcassets.

import sharp from 'sharp';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const assets = join(root, 'ios', 'App', 'App', 'Assets.xcassets');

const SPRUCE = '#1B3328';
const APRICOT = '#E0915C';
const MARK_PATHS = [
  'M12 20.4c-3.1 0-5.6-2.6-5.6-5.9 0-3.2 2.5-5.7 5.6-5.7s5.6 2.5 5.6 5.7c0 3.3-2.5 5.9-5.6 5.9z',
  'M12 9V6.2',
  'M12.5 7.1c.9-2.2 3.2-3.1 5.3-2.7.4 2.2-.7 4.3-2.8 4.7-1.4.3-2.5-.6-2.5-2z',
  'M12 10.2c-1.1 1.6-1.1 6.3 0 8.2',
];

// size: the square's side in px. markHeight: how tall the mark is, as a
// share of the square (0.42 is what icon-512.png uses). The 24-unit
// viewBox's drawn part runs from y=4.3 to y=20.4 and x=6.4 to x=17.9, so
// the box is centred on the drawing, not on the viewBox.
function markSvg(size, markHeight, strokeWidth) {
  const drawn = { x: 6.4, y: 4.3, w: 11.5, h: 16.1 };
  const scale = (size * markHeight) / drawn.h;
  const tx = size / 2 - (drawn.x + drawn.w / 2) * scale;
  const ty = size / 2 - (drawn.y + drawn.h / 2) * scale;
  const paths = MARK_PATHS.map((d) => `<path d="${d}"/>`).join('');
  return Buffer.from(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">` +
      `<rect width="${size}" height="${size}" fill="${SPRUCE}"/>` +
      `<g transform="translate(${tx} ${ty}) scale(${scale})" fill="none" stroke="${APRICOT}" ` +
      `stroke-width="${strokeWidth}" stroke-linecap="round" stroke-linejoin="round">${paths}</g>` +
      `</svg>`,
  );
}

async function write(svg, file) {
  // No alpha channel: App Store Connect rejects an app icon that has one.
  await sharp(svg).flatten({ background: SPRUCE }).removeAlpha().png().toFile(file);
  console.log('wrote', file.replace(root + '/', ''));
}

// App icon: one 1024 image; Xcode makes every smaller size from it.
await write(markSvg(1024, 0.42, 1.0), join(assets, 'AppIcon.appiconset', 'AppIcon-512@2x.png'));

// Launch image: 2732 square, shown "aspect fill", so the mark stays small
// and centred and the edges are plain spruce on every screen shape.
const splash = markSvg(2732, 0.085, 1.0);
for (const name of ['splash-2732x2732.png', 'splash-2732x2732-1.png', 'splash-2732x2732-2.png']) {
  await write(splash, join(assets, 'Splash.imageset', name));
}
