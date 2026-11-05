# study-area-map

一个给 AI agent 用的可复用技能（agent skill），用于生成**期刊级"研究区示意图"**（study-area map / location figure）。输入一个县级 adcode，输出一张多面板、全英文、Times New Roman 排版的投稿用图，含中国全图定位、区域位置、DEM 分层设色与山体阴影、实测水系与河名，以及可接入的本地专题栅格（示例：石漠化分级、地表覆盖）。

产出为 **300 dpi PNG + 矢量 PDF** 双份，并附一份带真实统计数字的图注草稿。

---

## 图面构成

| 面板 | 内容 | 数据来源 |
|---|---|---|
| (a) | 中国全图，省域青色高亮。含台湾、海南、南海诸岛与十段线，**画在图体内，不用附图框** | 阿里云 DataV + China-GeoData |
| (b) | 区域图，绘出目标省**全部县级边界**（贵州 88 县），研究县青底红边 | 阿里云 DataV（9 个市州 `_full.json` 合并） |
| (c) | DEM 分层设色 + 山体阴影 + 实测河道/水库 + 蓝色斜体河名 | AWS terrain tiles + OpenStreetMap |
| (d) | 专题分级栅格（示例为 2010 年石漠化强度分级） | 用户本地栅格 |
| (e) | 地表覆盖 / 土地利用（示例为 2025 年 30 m 产品） | 用户本地栅格 |
| (f-1) | 县界 + 邻县界 | 阿里云 DataV |
| (f-2) | DEM 提取的河谷骨架（与 (c) 来源不同，图内明确标注不可混用） | DEM |
| (f-3) | 高程—累计面积曲线 + 坡度分级面积柱 | DEM 现算 |

图例带里把**每个分级/类别的面积与百分比**直接印在图上，而不是只给色块。

## 排版约定

- 全图 Times New Roman；刻度标签加粗，主图 11 pt、定位图 9.5 pt
- 侧边纬度逆时针旋转 90°，格式去秒（`26°12′N`）
- 各面板上下边严格对齐到同两条水平基准线
- 多栏合并块的栏宽由研究区外包框的宽高比**反解**，栏间不留空白带
- 空白角落不用装饰填充，一律用同一份数据现算的真实统计补齐
- 投稿版图内文字全英文

完整规则见 [`style_spec.json`](./style_spec.json) 的 `current_defaults` 段与 [`SKILL.md`](./SKILL.md) 的"版式与排印规范"。

## 快速开始

```bash
# 1) 下载边界与 DEM，派生河谷骨架与坡度
python scripts/studyarea.py prepare --workdir ./work \
    --adcode 522729 --prefecture 522700 --province 520000

# 2) 出图（不接专题栅格 -> 自动降级为 (a)(b)(c)(f-1..3)）
python scripts/studyarea.py render --workdir ./work --out ./out \
    --adcode 522729 --prefecture 522700 --province 520000 \
    --title-c "Changshun county" --lang en

# 3) 接上本地专题栅格与采样点
python scripts/studyarea.py render --workdir ./work --out ./out \
    --adcode 522729 --prefecture 522700 --province 520000 \
    --title-c "Changshun county" --lang en \
    --themes examples/themes.example.json \
    --names  examples/hydro-en.example.json \
    --points examples/points.example.csv
```

adcode 查询：<https://datav.aliyun.com/portal/school/atlas/area_selector>。主题栅格配置见 `examples/themes.example.json`：给出文件路径（GeoTIFF，或 zip 内的 TIFF，自动用 `/vsizip/` 开窗读，不解压）、`nodata` 值、以及 `value → 标签 → 颜色` 列表；每个主题自动占一栏，并在图例带里印出**各类面积与百分比**。

`--lang en` 时若没给 `--names` 转写表，河名会**跳过并告警**，而不是把中文塞进全英文图里。

已验证：长顺 522729（含/不含专题栅格两条路径均出图成功；坡度分级面积合计 1532 km²，与县面积自检一致）。

> 未验证项（如实标注）：新入口只在长顺 522729 跑通；贵定 522723 的换区验证是在旧版三联入口上做的，本入口尚未换区重跑。小流域分割**不支持**（D8 洼地填充失败，(f-3) 用的是高程—面积曲线与坡度分级柱，不要按小流域分割图理解）。完整已知局限见 `SKILL.md` 的"当前已知局限"与 `PUBLISH_CHECKLIST.md`。

## 依赖

```bash
pip install -r requirements.txt
```

Python 3.10+。核心依赖：numpy / scipy / matplotlib / Pillow / pyshp / shapely / rasterio。
若本机 GDAL 缺 ADF 驱动而需要读 ArcInfo GRID，可用 QGIS 自带的 `gdal_translate`（见 SKILL.md）。

## 数据来源与许可

| 数据 | 许可 | 使用要求 |
|---|---|---|
| 行政边界 | 阿里云 DataV 地理信息公共服务 | 遵守其服务条款，注意数据时效 |
| 高程 | AWS Terrain Tiles（Mapzen / Tilezen，terrarium 编码） | 上游为 SRTM/ALOS 等，注明源与分辨率 |
| 大河水系 | Natural Earth | 公有领域 |
| 河道、水库、河名 | OpenStreetMap（Geofabrik 提取） | **ODbL，须署名 © OpenStreetMap contributors** |
| 十段线 | China-GeoData（GitHub 开源） | 核对原仓库许可 |

本技能自身代码采用 MIT 许可（见 [LICENSE](./LICENSE)），**上述数据许可独立计算，不随代码转移**。

## 关于样式出处（请务必读完）

本技能里的样式变体**并非全部来自已核实的正刊图页**：

- `nature_earth`、`pastel_tectonic` 的配色是 agent 自选方案；
- v9 之后的多面板版式，出处是**用户提供的参考图**；
- 只有 Nature 623 (2023) Fig.1（手稿 p22）经过逐页打开核实，确认是真实的研究区图。

早期版本曾把自创样式声称为正刊逆向结果，该错误已在 `style_spec.json` 的 `provenance_correction_2026_11_04` 字段中公开更正。复用或二次分发时请保留这段声明。

## 合规提醒

含中国全图的图件公开发表需要**审图号**（自然资源部标准地图服务系统）。本技能生成的图**未经审图**，用于投稿前请替换为标准底图或取得审图号。南海诸岛与十段线的完整表示已内置，但不能替代审图程序。

## 目录结构

```
study-area-map/
├── SKILL.md              # agent 用的技能说明书（含版式规范与已知局限）
├── style_spec.json       # 风格卡（审美与排版规范，current_defaults 段为唯一权威值）
├── requirements.txt
├── LICENSE               # MIT（代码与样式规范；数据许可见 LICENSE 末尾说明）
├── CHANGELOG.md
├── PUBLISH_CHECKLIST.md  # 发布前待办与未核实项
├── scripts/
│   ├── studyarea.py      # 唯一入口：prepare（取数与派生） / render（出图）
│   └── osm_clip.py       # 可选：本地 OSM 水系按县界裁剪 → rivers.json / water.json
└── examples/
    ├── example-changshun.png                  # 含两份专题栅格的完整六面板产出
    ├── example-without-theme-rasters.png      # 不给 --themes 的降级版式
    ├── example-figure-caption.md              # 带真实统计数字的图注范例
    ├── themes.example.json                    # 专题栅格配置（路径/nodata/值→标签→颜色）
    ├── hydro-en.example.json                  # 河名拼音转写表
    └── points.example.csv                     # 采样点 lon,lat,label
```

`scripts/` 只有这两个文件，且**都不含绝对路径**。v5–v17 的逐版渲染脚本是开发过程的中间产物，写死了作者本机的数据路径，**不随本仓库分发**；版式演进过程记录在 `CHANGELOG.md` 里。

## 作为 QwenWork 技能安装

把整个目录放到 `~/.qwenworkcn/skills/study-area-map/`（Windows：`%USERPROFILE%\.qwenworkcn\skills\study-area-map\`），agent 会按 `SKILL.md` 的 frontmatter 自动识别。其他 agent 框架可直接读 `SKILL.md` 作为 system 级指令使用。

## 贡献与反馈

欢迎提 issue。若你改进了排版或数据管线，请在 `CHANGELOG.md` 里说明**改了哪张图、为什么**，并附一张前后对比图——这个技能的每条规则都来自一次实际的返工。
