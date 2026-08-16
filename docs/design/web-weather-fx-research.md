# 网页端天气特效：行业做法调研（简报）

**日期:** 2026-07-30  
**结论:** 聊天/落地页类「背景雨雪」以 **Canvas 2D 粒子循环** 或 **CSS/轻量库** 为主，不必上 GPU 着色器。

---

## 1. 同类需求场景

| 场景 | 常见做法 |
|------|----------|
| 官网/落地页「下雪」 | Canvas 或 `requestAnimationFrame` 画圆点下落 |
| 节日活动页 | 同上；或 [tsparticles](https://particles.js.org/) 配置 JSON |
| 轻游戏/桌宠 UI | 精灵小图 + 粒子发射器；或纯几何点/线 |
| 地图/天气 App | 色调滤镜 + 可选粒子层 |
| 重度游戏 | Unity/UE 粒子 + 贴图（超出网页聊天背景预算） |

---

## 2. 技术路线对比（前端）

| 方案 | 质量 | 难度 | 素材 | 适合我们？ |
|------|------|------|------|------------|
| **Canvas 2D 粒子** | 中高（运动真实） | 低 | 可不依赖 | **首选** |
| **tsparticles / particles.js** | 中高 | 低 | 可选形状 | 可后续引入 |
| **CSS 动画 + 多 span** | 低中 | 低 | 可用 emoji/图 | 粒子多时卡 |
| **WebGL/Shader** | 高上限 | 高 | 可不依赖 | 调参难、易像贴图 |
| **循环视频 WebM** | 高像素质感 | 低 | 需视频 | 体积/循环缝；已否决 |
| **雪花/雨丝 PNG 精灵** | 形状好看 | 低 | Kenney/OGA | 可增强，非必须 |

---

## 3. 素材从哪来（可选）

- [Kenney.nl](https://kenney.nl/assets) Particle Pack（多 CC0）
- [OpenGameArt](https://opengameart.org) 搜 snowflake / rain
- [itch.io free assets](https://itch.io/game-assets/free)

**雨丝/基础雪点：代码几何即可**；要「六瓣雪花」再上小 PNG。

---

## 4. 本项目采用

1. **CSS** `stageBaseBackground`：时段/天气色幕（整窗）  
2. **Canvas 2D** `CanvasWeather`：雨线 / 雪点 / 雾团，随机属性 + 从上往下  
3. WebGL 路径保留文件但不作为 App 默认  

验收：面板 `v6-canvas2d`；雪/雨明显下落；调「雨雪量」改粒子数量而非格子密度。
