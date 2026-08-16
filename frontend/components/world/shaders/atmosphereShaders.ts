/**
 * Full-screen WebGL2 atmosphere (no textures):
 * - Fog: FBM + domain warp + curl
 * - Rain/Snow: noise-field precipitation falling top→bottom (not grid wallpaper)
 */

export const ATMOSPHERE_VERT = `#version 300 es
precision highp float;
layout(location = 0) in vec2 aPos;
out vec2 vUv;
void main() {
  vUv = aPos * 0.5 + 0.5;
  gl_Position = vec4(aPos, 0.0, 1.0);
}
`;

export const ATMOSPHERE_FRAG = `#version 300 es
precision highp float;

in vec2 vUv;
out vec4 fragColor;

uniform float uTime;
uniform float uSeed;
uniform float uIntensity;
uniform float uCloudDensity;
uniform float uContrast;
uniform float uGoldTint;
uniform float uAlphaCap;
uniform float uExposure;
uniform float uWarmth;
uniform float uMistBias;
uniform float uCloudContrast;
uniform vec4 uEvent0;
uniform vec4 uEvent1;
uniform vec2 uResolution;
uniform vec3 uMouse;
// 0=clear 1=mist 2=rain 3=snow
uniform float uWeatherMode;
uniform float uMotionSpeed;
uniform float uDensityMul;
uniform float uPrecipMul;
uniform float uFogMul;
uniform float uWind;

// -------- hash / noise / FBM --------
float hash21(vec2 p) {
  p = fract(p * vec2(123.34, 456.21) + uSeed * 0.00137);
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

vec2 hash22(vec2 p) {
  float n = hash21(p);
  return vec2(n, hash21(p + n * 17.13 + 31.7));
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  float a = hash21(i);
  float b = hash21(i + vec2(1.0, 0.0));
  float c = hash21(i + vec2(0.0, 1.0));
  float d = hash21(i + vec2(1.0, 1.0));
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

float fbm(vec2 p) {
  float v = 0.0;
  float a = 0.5;
  mat2 m = mat2(1.6, 1.2, -1.2, 1.6);
  for (int i = 0; i < 5; i++) {
    v += a * noise(p);
    p = m * p;
    a *= 0.5;
  }
  return v;
}

vec2 curlOffset(vec2 p, float t) {
  float e = 0.08;
  float n1 = fbm(p + vec2(0.0, e) + t);
  float n2 = fbm(p - vec2(0.0, e) + t);
  float n3 = fbm(p + vec2(e, 0.0) + t);
  float n4 = fbm(p - vec2(e, 0.0) + t);
  return vec2((n1 - n2) / (2.0 * e), -(n3 - n4) / (2.0 * e));
}

float eventBoost(vec2 uv, vec4 e) {
  if (e.z <= 0.001 || e.w <= 0.001) return 0.0;
  float d = distance(uv, e.xy);
  float g = 1.0 - smoothstep(0.0, e.z, d);
  return g * g * e.w;
}

float readabilityFalloff(vec2 uv) {
  vec2 c = uv - vec2(0.5, 0.48);
  c.x *= 1.15;
  c.y *= 0.85;
  float r = length(c);
  // keep precip more visible than before; light center dip only
  return mix(0.72, 1.0, smoothstep(0.08, 0.5, r));
}

/*
 * UV: y=0 bottom, y=1 top.
 * Falling DOWN = features move toward smaller y
 * ⇒ sample with (uv.y + t * speed) so pattern scrolls top→bottom.
 */

// -------- RAIN: elongated noise ridges + warp (not a regular lattice) --------
float rainNoiseLayer(vec2 uv, float t, float amount, float speed, float slant, float layer) {
  float spd = max(uMotionSpeed, 0.12) * speed;
  // organic warp so columns never line up
  float warp = fbm(uv * (2.4 + layer * 0.7) + vec2(layer * 5.1, t * 0.07));
  float warp2 = fbm(uv * 5.0 - vec2(t * 0.05, layer * 3.0));

  vec2 p = uv;
  // wind slant + FBM lateral jitter
  p.x += p.y * slant + warp * 0.12 + warp2 * 0.05 + uWind * 0.08;
  // TOP → BOTTOM fall
  p.y += t * spd;

  // Anisotropic stretch: high freq in X (many threads), low in Y (elongated drops)
  // incommensurate scales per layer break tiling
  float sx = mix(55.0, 110.0, amount) * (1.0 + layer * 0.37);
  float sy = mix(9.0, 16.0, amount) * (1.0 + layer * 0.21);
  vec2 q = vec2(p.x * sx + layer * 19.7, p.y * sy);

  float n = noise(q);
  float nDetail = noise(q * 2.3 + 8.1);
  // ridge-like: only rare bright peaks → sparse organic streaks
  // amount controls threshold (low amount = rarer peaks, still organic)
  float thr = mix(0.93, 0.72, clamp(amount, 0.0, 1.5) / 1.5);
  float drop = smoothstep(thr, thr + 0.06, n) * smoothstep(0.35, 0.85, nDetail);

  // length modulation along fall axis (broken streaks, not wallpaper lines)
  float lenMod = smoothstep(0.15, 0.55, noise(vec2(q.x * 0.35, q.y * 0.9 + 2.0)));
  drop *= lenMod;

  // occasional denser sheets of rain (noise weather fronts)
  float sheet = smoothstep(0.55, 0.9, fbm(vec2(uv.x * 1.5 + layer, t * 0.12 + layer)));
  drop *= mix(0.55, 1.25, sheet);

  return drop;
}

vec4 rainFX(vec2 uv, float t) {
  float amount = clamp(uPrecipMul, 0.0, 2.5);
  if (amount < 0.02) return vec4(0.0);

  float slant = 0.18 + uWind * 0.55;
  float r = 0.0;
  r += rainNoiseLayer(uv, t, amount, 0.55, slant, 0.0);
  r += rainNoiseLayer(uv, t, amount * 0.9, 0.78, slant * 1.12, 1.0) * 0.65;
  r += rainNoiseLayer(uv, t, amount * 0.75, 0.42, slant * 0.88, 2.2) * 0.45;
  r += rainNoiseLayer(uv, t, amount * 0.55, 0.95, slant * 1.25, 3.7) * 0.3;

  r *= readabilityFalloff(uv);
  // soft blue-silver rain, not a solid texture plate
  vec3 col = mix(vec3(0.55, 0.62, 0.75), vec3(0.82, 0.88, 0.95), 0.55);
  float a = clamp(r * (0.55 + 0.35 * amount), 0.0, 0.78);
  return vec4(col, a);
}

// -------- SNOW: multi-scale jittered voronoi flakes + FBM drift --------
float snowLayer(vec2 uv, float t, float amount, float speed, float cellScale, float layer) {
  float spd = max(uMotionSpeed, 0.12) * speed;
  // FBM warp destroys regular lattice look
  vec2 warp = vec2(
    fbm(uv * 2.1 + layer * 4.0 + t * 0.04),
    fbm(uv * 2.3 - layer * 2.7 - t * 0.03)
  );
  vec2 p = uv + (warp - 0.5) * 0.22;

  // sway + wind (horizontal only)
  p.x += sin(t * (0.35 + layer * 0.11) + p.y * 4.5 + layer * 2.0)
    * (0.02 + uWind * 0.06);
  p.x += uWind * t * 0.03 * spd;
  // TOP → BOTTOM fall
  p.y += t * spd;

  // non-integer scale ratios (avoid commensurate tiling)
  float sc = cellScale * (0.85 + 0.3 * hash21(vec2(layer, uSeed)));
  vec2 g = floor(p * sc);
  float acc = 0.0;

  // 3x3 neighbor search for irregular flake centers
  for (int oy = -1; oy <= 1; oy++) {
    for (int ox = -1; ox <= 1; ox++) {
      vec2 cell = g + vec2(float(ox), float(oy));
      vec2 rnd = hash22(cell + layer * 13.0);
      // density via probability — low amount = fewer flakes, still random places
      float spawn = hash21(cell + 9.7 + layer);
      float thr = mix(0.92, 0.45, clamp(amount, 0.0, 1.8) / 1.8);
      if (spawn < thr) {
        // skip this cell
      } else {
        // random position inside cell (full jitter)
        vec2 center = (cell + rnd) / sc;
        vec2 d = p - center;
        // slight elliptical flake
        d.x *= 1.1;
        d.y *= 0.95;
        float rad = (0.0035 + rnd.x * 0.012) * mix(0.7, 1.4, amount * 0.5);
        // depth layers: farther = smaller/dimmer
        float depth = 0.55 + 0.45 * rnd.y;
        rad *= mix(0.65, 1.2, depth);
        float dist = length(d);
        float flake = smoothstep(rad, rad * 0.15, dist);
        flake += smoothstep(rad * 0.45, 0.0, dist) * 0.4;
        acc += flake * depth * (0.55 + 0.45 * hash21(cell + 2.2));
      }
    }
  }
  return acc;
}

vec4 snowFX(vec2 uv, float t) {
  float amount = clamp(uPrecipMul, 0.0, 2.5);
  if (amount < 0.02) return vec4(0.0);

  float s = 0.0;
  // incommensurate scales + speeds (Fibonacci-ish)
  s += snowLayer(uv, t, amount, 0.09, 14.0, 0.0);
  s += snowLayer(uv, t, amount * 0.9, 0.14, 23.0, 1.0) * 0.75;
  s += snowLayer(uv, t, amount * 0.75, 0.06, 9.0, 2.0) * 0.9;
  s += snowLayer(uv, t, amount * 0.6, 0.19, 31.0, 3.0) * 0.5;

  // soft veil of distant snow (FBM field, not grid)
  float veil = fbm(vec2(uv.x * 3.0 + t * 0.05, uv.y + t * 0.08 * max(uMotionSpeed, 0.12)));
  veil = smoothstep(mix(0.75, 0.5, amount / 2.0), 0.95, veil) * 0.15 * amount;
  s += veil;

  s *= readabilityFalloff(uv);
  vec3 col = mix(vec3(0.88, 0.9, 0.94), vec3(1.0, 0.98, 0.94), 0.2 * uWarmth);
  float a = clamp(s * (0.45 + 0.3 * amount), 0.0, 0.82);
  return vec4(col, a);
}

void main() {
  vec2 uv = vUv;
  float aspect = uResolution.x / max(uResolution.y, 1.0);
  vec2 p = uv * vec2(aspect, 1.0);

  float t = uTime * 0.018 * max(uMotionSpeed, 0.12);
  float fogAmt = max(uFogMul, 0.0) * uDensityMul;

  vec2 curl = curlOffset(p * 1.1 + uSeed * 0.01, t * 0.4);
  p += curl * (0.1 + 0.08 * fogAmt);
  p.x += uWind * t * 0.35;

  if (uMouse.z > 0.001) {
    vec2 m = uMouse.xy * vec2(aspect, 1.0);
    vec2 d = p - m;
    float dist = length(d);
    float rad = 0.12 * aspect;
    float fall = exp(-(dist * dist) / max(rad * rad, 1e-4));
    float force = fall * uMouse.z * 0.35;
    vec2 tang = dist > 1e-4 ? vec2(-d.y, d.x) / dist : vec2(0.0);
    p += tang * force * 0.08;
    p += normalize(d + 1e-4) * force * 0.03;
  }

  vec2 q = vec2(fbm(p * 1.2 + t + uSeed), fbm(p * 1.2 + 5.2 - t * 0.7));
  vec2 r = vec2(
    fbm(p * 2.0 + q * 1.15 + t * 0.35),
    fbm(p * 2.0 + q * 1.15 + vec2(1.7, 9.2) - t * 0.25)
  );

  float n = fbm(p * 1.6 + r * 0.85 + t * 0.15);
  n = pow(clamp(n, 0.0, 1.0), mix(1.4, 0.85, uContrast * 0.5));

  float sky = smoothstep(1.05, 0.15, uv.y);
  float density = n * mix(0.55, 1.0, sky);
  density += eventBoost(uv, uEvent0) * 1.25;
  density += eventBoost(uv, uEvent1) * 1.25;
  density += uMistBias * 0.55 * (0.45 + 0.55 * fbm(p * 0.8 - t));

  float modeFog = 1.0;
  if (uWeatherMode > 0.5 && uWeatherMode < 1.5) modeFog = 1.45;
  if (uWeatherMode > 1.5 && uWeatherMode < 2.5) modeFog = 1.12;
  if (uWeatherMode > 2.5) modeFog = 1.08;

  density *= uCloudDensity * uCloudContrast * fogAmt * modeFog;
  density *= readabilityFalloff(uv);
  density = clamp(density, 0.0, 1.0);

  vec3 ink = vec3(0.14, 0.13, 0.12);
  vec3 gold = vec3(0.82, 0.7, 0.45);
  float gMix = density * uGoldTint * (0.45 + 0.55 * uWarmth);
  vec3 fogCol = mix(ink, gold, clamp(gMix, 0.0, 0.9));
  fogCol *= uExposure;
  float fogA = density * max(uIntensity, 0.55) * uAlphaCap;
  fogA = clamp(fogA * 1.1, 0.0, min(0.65, uAlphaCap * 1.1));

  // rain/snow: reduce base fog so precip reads as motion not flat sheet
  if (uWeatherMode > 1.5) {
    fogA *= 0.55;
  }

  vec4 fog = vec4(fogCol, fogA);

  vec4 precip = vec4(0.0);
  // use raw time so motionSpeed inside layers stays consistent
  float pt = uTime;
  if (uWeatherMode > 1.5 && uWeatherMode < 2.5 && uPrecipMul > 0.01) {
    precip = rainFX(uv, pt);
  } else if (uWeatherMode > 2.5 && uPrecipMul > 0.01) {
    precip = snowFX(uv, pt);
  }

  vec3 outRgb = fog.rgb * fog.a * (1.0 - precip.a) + precip.rgb * precip.a;
  float outA = fog.a + precip.a * (1.0 - fog.a);
  if (outA < 1e-4 && precip.a > 0.0) {
    outRgb = precip.rgb;
    outA = precip.a;
  } else if (outA > 1e-4) {
    outRgb /= max(outA, 1e-4);
  }

  fragColor = vec4(outRgb, clamp(outA, 0.0, 0.9));
}
`;
