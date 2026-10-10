# 守望 · RareGuard

面向 SJA（全身型幼年特发性关节炎）患儿家庭的 **MAS（巨噬细胞激活综合征）并发症动态预警平台**。
「罕见·无界黑客松」赛题 04 参赛作品（2026.10.15–10.17 杭州）。

## 核心设计

风险结论 **100% 出自确定性规则引擎**（绝对阈值 + 趋势斜率 + MAS 八项评分），LLM 仅将规则结论翻译为家长能读懂的表达，不参与任何判断——系统断网照常出预警。

```
家属端 Web PWA（拍照录入 / 每日打卡 / 仪表盘 / 预警详情）
   ▼
FastAPI 网关（角色裁剪 + 审计）
   ▼
OCR 解析（Qwen-VL，双通道校验，W2）│ 打卡表单
   ▼
SQLite/PG 时序库（TimeSeriesProvider 只读抽象）
   ▼
规则引擎 R-A 阈值 / R-B 趋势 / R-C MAS 评分（不经 LLM）
   ▼
校验管道（V1 输入净化 / 接地 / 护栏 / PHI + 审计；键名历史兼容）→ 红/黄/绿分级预警 + WORM 审计
```

## 自主性定位：L1 辅助预警（非 L3）

本系统按医学智能体光谱刻意定位为 **自主性 L1 辅助预警**（仅此语境使用「自主性 L1」，勿与校验管道 V1 输入净化混称）：

- **规则主权**：红/黄/绿与 MAS 风险分仅由确定性规则引擎给出（`decision_owner: rules_engine`）；接口 meta 亦声明 `autonomy_level: "L1"`。
- **LLM 仅叙述**：大模型只把结构化结论改写成家长可读文案，并强制过校验管道；**不接入 analysis/risk 判险路径**。
- 对抗「罕见表型被通用模型抹除」：用可审计规则 + 长尾/MAS 前驱**合成留出回归门禁**（回归门禁 ≠ 临床金标），避免把罕见病早期信号交给通用 LLM 凭语感抹平。
- **明确不做**：不做 L3 独立坐诊；不做权重级自我进化（在线改规则/改模型权重）；不做 FHIR/DICOM 全量互通。

上述口径借用临床自我进化系统综述（arXiv:2607.11175，Zhu et al.）中的自主性光谱作**产品定位解读**，并非声称已实现该文的自我进化或 L3 架构。

## 开发复现

```bash
pip install -r requirements.txt
python -m pytest -q            # 全量单测 + 评测门禁（real_api 标记自动跳过）
python -m synth.generate_dataset  # 重新生成合成评测集（seed=42 可复现）
```

## 本地运行与演示

**2026-10-10 工程补齐**：正常启动需要 `RAREGUARD_ACCOUNTS_FILE` 家庭账号配置；未认证演示须同时声明 `RAREGUARD_DEMO=1` 与 `RAREGUARD_OFFLINE=1`，且只使用合成数据。账号创建、登录、患者隔离、真实样例验收及部署步骤见 [试点工程交接](docs/pilot-engineering-handoff.md)。本项目开发代码在 `master`，旧 `main` 仅为原始赛题说明。

参赛启动**只**用家庭端（基座预问诊 `rareguard.api.app` 为遗留，非本赛题主路径）。

```bash
# 在线模式（.env 配置魔搭 OpenAI 兼容端点，密钥不入库）
python -m rareguard.api.home_server

# 断网演示模式：OCR 走预跑缓存，叙述回退确定性模板，规则引擎照常出分
RAREGUARD_DEMO=1 RAREGUARD_OFFLINE=1 python -m rareguard.api.home_server

# 端到端演示脚本（零网络，输出建档→打卡→OCR→趋势→红色预警全链路 JSON）
python scripts/demo_e2e.py

# 播种近三日「绿→黄→红」合成高危样例，供彩排与截图取证（数据不入库）
python scripts/seed_demo_db.py data/demo/rareguard_demo.db
```

浏览器打开 `http://127.0.0.1:8000/` 即家庭端四屏 PWA（单文件、零外部依赖）。
带 `?pid=` 深链（如 `/?pid=PDEMO`）可自动选中患儿并直接呈现当前风险，便于分享与无头取证。

预警详情屏可「生成就诊一页纸」——`GET /api/home/summary/{pid}` 返回内联趋势图 + 异常化验表 + 命中规则的打印友好摘要，供家长带去急诊。

效果预览（由播种脚本 + 无头浏览器自动截取）：`docs/assets/01-red-alert.png`、`docs/assets/02-one-pager.png`。

## 彩排与发布门禁（长尾留出 / 罕见表型回归）

**合成留出是回归门禁，不是临床金标。** 评测把 MAS 前驱等长尾表型留在合成集上，用确定性指标阻断发布——门禁失败 = 不得交付；下列指标**不是**真实患儿临床验证。

| 角色 | 路径 | 作用 |
|---|---|---|
| 合成留出集 **N=6** | `synth/generate_dataset.py` | seed=42 可复现；2 例 MAS 前驱 + 4 例正常对照，供罕见表型回归门禁（不是临床金标） |
| W1 指标门禁 | `evals/gate_rareguard.py` | 合成集上召回 100% / 提前量 ≥48h / 误报 ≤1 次·患者·月；不达标即阻断 |
| 一键编排 | `scripts/run_all_gates.py` | 召回/OCR（合成）/红线/断网 E2E/真实冒烟；任一 fail 退出码 1 |

```bash
# 赛前联网预跑一次：解析合成化验单并落 OCR 缓存（生成 data/demo/lab_report.png）
python scripts/prime_offline_cache.py

# 一键五道门禁（合成召回/合成OCR/红线/断网E2E/真实冒烟），任一 fail 退出码 1
python scripts/run_all_gates.py

# 真实脱敏样例到位后（本地 data/real_samples/，不入库）再报字段准确率；合成 OCR≥98% 不能替代本步
python -m evals.real_ocr_eval
```

现场操作与回退预案见 `docs/rehearsal-checklist.md`，10 分钟路演分镜见 `docs/roadshow-storyboard.md`。提升改造三阶段计划见 `docs/roadmap-upgrade.md`。

- 设计规格：`docs/superpowers/specs/2026-09-21-rareguard-t04-design.md`
- 实施计划：`docs/superpowers/plans/`
- 规则取值文献：`docs/references.md`
- 官方 PRD 对照清单（已实现 / 明确延期）：`docs/prd-v1-gap-checklist.md`
- 患者共创访谈提纲：`docs/patient-cocreation-interview-guide.md`
- 合规与落地说明：`docs/compliance-deployment-onepager.md`

## 安全红线

不确诊、不给用药方案、所有 AI 输出强制"以医生诊断为准"标记；演示数据全部为合成数据。

## License

Apache-2.0
