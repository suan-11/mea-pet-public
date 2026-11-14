# 产物发布与安全更新机制（P0-3）

> 对应路线图 `docs/development-suggestions.zh-CN.md` §3 P0-3 与 §7 风险登记册
> 「P0-3 更新覆盖用户 config.json 与 mea_memory.db（高/高）」。
> 本文档是 Release 流程、应用内检查更新行为与数据保护白名单的契约真值。

---

## 1. Release 流程（tag → 可校验产物）

### 1.1 自动流水线

`.github/workflows/release.yml`：推 `v*` tag 触发，在 `windows-latest` +
Python 3.13 上执行：

1. checkout（`fetch-depth: 0` 取全历史与 tag；`lfs: true` 水化模型权重——
   构建脚本有「拒绝打包 LFS 指针」闸门，core 档虽不把权重打进包，闸门仍读那些文件）；
2. 安装构建依赖：`pip install -e ".[win32,opengl,webengine]" pyinstaller`，
   另显式装 `numpy>=2.0`（bootstrap `--check all` 的
   `_OPTIONAL_SOURCE_DEPENDENCIES` 含 numpy，core 运行时也保留它；
   3.13 需要 2.x 轮子）。**core 档不需要 torch/scipy**——VITS/GSV 重栈走
   外置扩展包（P0-2）；
3. 设 `MEAPET_PROFILE=core` 调 `scripts/build_windows.ps1`（staging + 后置断言 +
   core 瘦身由主代理 P0-1/P0-2 改造，本流水线按「脚本产出合规 dist/MeaPet」消费）；
4. 从 tag 解析版本（必须 `vMAJOR.MINOR.PATCH` 形状，否则拒绝发布），把
   `dist/MeaPet` 打成 **`MeaPet-win64-core-vX.Y.Z.zip`**（zip 根保留 `MeaPet/`
   目录，解压即用）；
5. 生成 **`SHA256SUMS.txt`**（sha256sum 兼容格式：`<hash>  <文件名>`）；
6. 用 `git log 上一个tag..HEAD` 自动生成 CHANGELOG 摘要作为 Release Notes
   （首个 tag 无上一个 tag 时用完整历史）；
7. `softprops/action-gh-release@v2` 发布 GitHub Release，附 zip 与 SHA256SUMS.txt。

### 1.2 发版操作（维护者）

```bash
# 0) 先同步 meapet/__init__.py 的 __version__ 与将要打的 tag（见 §1.4）
# 1) 确认 main 绿（CI 的 lint/compileall/pytest 全过）
git tag v1.7.0
git push origin v1.7.0
# 2) 等 Release 工作流跑完，在 Releases 页核对三件套：zip + SHA256SUMS.txt + Notes
```

用户侧校验：

```powershell
Get-FileHash -Algorithm SHA256 MeaPet-win64-core-v1.7.0.zip
# 与 SHA256SUMS.txt 中的值比对，一致才解压
```

### 1.3 诚实边界（判不了就说判不了）

- 本工作流目前只做到**静态审查级**（YAML 语法校验 + 逐行审查），没有真推
  tag 跑过 CI；验收判据①「每次打 tag 自动产可校验 zip + sha256 + Release
  Notes」需在**首个真实 tag** 上复核。
- 现行 `scripts/build_windows.ps1` 的 `python -m meapet.bootstrap --check all`
  门禁还会要求 core extras 之外的 `live2d-py` 等包；该门禁的 core 档放宽
  属于主代理的 P0-1/P0-2 改造（`meapet/bootstrap.py` 与构建脚本不在 P0-3
  的文件清单里）。若首个 tag 在门禁处红，先确认主代理的改造是否已合入。

### 1.4 版本号现状（实测读数）

仓库当前 `meapet.__version__ = "1.0.0"`，而上游已有 `v1.4.1`、`v1.6.0`
两个 tag——**两者已经漂移**。检查更新按「latest release tag vs
`__version__`」比较，漂移期间旧包会一直提示有更新（行为正确，但发版前
应把 `__version__` 提到与 tag 一致，这是发版管理动作，不在 P0-3 代码范围）。

---

## 2. 应用内检查更新（只提示，绝不静默升级）

实现：`meapet/update_check.py`；入口：右键菜单「设置与数据 → 检查更新…」
（`meapet/desktop/window_chrome.py` 的 `_build_context_menu()` 是菜单结构
与文案的唯一来源，`menu_window.py` 只是渲染器，无需改动）。

### 2.1 行为契约

| 项 | 约定 |
|---|---|
| 上游 | `https://api.github.com/repos/NekoHome-Studio/mea-pet-public/releases/latest`（真实上游；pyproject 里的 suan-11 URL 是旧地址，不用） |
| 触发 | 仅手动菜单项；**没有定时自动检查**，因此不存在弹窗轰炸 |
| 线程 | `UpdateCheckWorker(QThread)` 后台拉取，结果经信号回 GUI 线程弹 `QMessageBox` 信息框，不阻塞 UI |
| 超时 | 10s（`CHECK_TIMEOUT_SECONDS`）；TLS 走 OS 证书库（`ssl.create_default_context()`，与 `meapet/http_async.py` 同款，避冻结态 certifi 缺失）；UA 用 `meapet.USER_AGENT` |
| 版本比较 | `parse_version` 容错 v 前缀/非数字段；数字段按位比、短侧补 0；解析不出就报「判不了」，不猜 |
| 结果三态 | 有更新 / 已是最新 / 检查失败（带原因） |
| 有更新时展示 | 更新说明（截断 800 字符）、资产名、sha256（先读 API `digest` 字段，退到 `SHA256SUMS.txt` 文本资产）、下载页链接 + 「打开下载页」按钮（浏览器） |
| 失败降级 | 记 `logging` 日志、返回 error 结果；手动入口弹一次含原因的信息框，不抛异常、不连环弹窗 |
| **升级动作** | **绝不下载安装包、绝不覆盖任何文件、绝不执行任何升级动作**。网络上只取 release JSON 与 SHA256SUMS.txt 两份小文本；测试钉死「任何 GET 都不指向 zip」 |

### 2.2 用户侧更新姿势（当前版本）

检查更新只负责「告诉你有新版、给你校验和与下载链接」。实际更新由用户
手动完成：下载 zip → 核对 SHA256 → 按 §4 演练步骤覆盖（**跳过白名单路径**）。
未来的自动更新器必须消费 §3 的白名单契约；`tufup` / WinSparkle 增量引擎
是可选演进，不阻塞首版。

---

## 3. 数据保护白名单契约（`meapet/update_policy.py`）

便携契约（`tests/test_frozen_paths.py` 锁定，**不改**）：冻结态数据根是
`sys._MEIPASS`（`dist/MeaPet/_internal`），用户数据随发行文件夹走。
因此任何「覆盖式更新」必须先过白名单闸门。

### 3.1 受保护路径（唯一真值表 `UPDATE_PROTECTED`）

| 条目 | 含义 |
|---|---|
| `config.json` | 用户配置（数据根下，精确匹配） |
| `mea_memory.db*` | 记忆库主文件与 `-wal`/`-shm`/`-journal` 等边车（前缀匹配） |
| `logs/` | 全部日志 |
| `voice_cache/` | 语音缓存 |
| `voice_asr/` | ASR 模型缓存 |
| `addons/` | 扩展包 |
| `audio_cache/` | TTS 音频缓存 |

判定口径：相对数据根的路径；反斜杠归一为正斜杠；大小写不敏感（Windows）；
容忍 `_internal/` 前缀（更新器从 `dist/MeaPet` 根枚举时剥掉再判）。
**只认数据根这一层**：程序资产里的同名文件（如 `meapet/assets/config.json`、
`config.example.json`）不受保护，更新可正常覆盖。

### 3.2 API

- `is_protected_relpath(rel) -> bool`：单条判定。
- `assert_update_safe(relpaths) -> None`：未来更新器**落盘前必须整表调用**的
  闸门。任何受保护路径、绝对路径、`..` 逃逸、空路径出现即抛
  `UpdateProtectedError`（fail-closed：口径不明同样拒绝），一次列全违规项。

测试钉子：`tests/test_update_policy.py`（含验收判据②：覆盖清单含
`config.json` / `mea_memory.db` 时必被拒）。改白名单必须先有意识地改
测试与本文档。

### 3.3 迁移预留（长远）

路线图要求后续把 `userdata/` 与 `addons/` 抽离 `_internal`、放到
`MeaPet.exe` 同级目录。届时契约语义不变，只需把判定根扩成两处（数据根 +
exe 同级目录），并参照 `meapet/paths.py:migrate_legacy_home_data()` 的先例
做「只补缺、不覆盖」的一次性迁移。在那之前，白名单就是更新器的硬边界。

---

## 4. 验收演练：覆盖更新后用户数据必须完好

> 验收判据②：覆盖更新演练后现有 `config.json` 与 `mea_memory.db` 100% 完好。
> 以下是人工演练清单（需要真实两版包；本文档落地时未执行，判不了实测结果）。

1. **准备旧包**：解压一份 `MeaPet-win64-core-vOLD.zip` 到 `演练/MeaPet`，
   启动一次并改几项设置、聊几轮天，让它生成 `_internal/config.json` 与
   `_internal/mea_memory.db`（含 `-wal`/`-shm`），正常退出。
2. **录指纹**：
   ```powershell
   Get-FileHash 演练\MeaPet\_internal\config.json, 演练\MeaPet\_internal\mea_memory.db -Algorithm SHA256
   ```
3. **干跑覆盖清单**：把「新包相对旧包要覆盖的文件清单」整表过闸门：
   ```python
   from meapet.update_policy import assert_update_safe
   assert_update_safe(覆盖清单)  # 含 config.json / mea_memory.db* 必抛 UpdateProtectedError
   ```
   故意把 `config.json` 塞进清单，确认**必被拒**（这个负例必须红得出来）。
4. **执行覆盖**：解压新 zip 到临时目录，只把**不在白名单内**的文件复制进
   旧包（未来更新器自动做这一步；当前手动用 robocopy 排除或脚本过滤）。
5. **复核指纹**：重跑第 2 步，两个哈希必须与覆盖前**逐字节一致**；
   `mea_memory.db-wal`/`-shm` 若存在同样不得被触碰。
6. **完整性与功能**：
   ```powershell
   python -c "import sqlite3; c=sqlite3.connect(r'演练\MeaPet\_internal\mea_memory.db'); print(c.execute('PRAGMA integrity_check').fetchone())"
   ```
   期望 `('ok',)`；再启动新包，确认设置与记忆都在。
7. **留档**：把前后哈希、integrity_check 输出与结论记入发版记录。

---

## 5. 本机制不能证明什么（诚实清单）

- release.yml 未真跑 CI（静态审查级）；首个真实 tag 前，判不了「自动产
  Release」是否成立。
- 检查更新的 HTTP 全在 mock 下测过；真实 GitHub API 的可达性、限流
  （未认证 60 次/小时/IP）与真实资产形状未实测。
- 覆盖演练（§4）是人工清单，本文档落地时未执行；白名单测试只证契约层
  「必被拒」，不证真实更新器行为（更新器尚未实现）。
- 冻结态（PyInstaller）下 QThread/httpx/OS 证书库的行为未实测，属构建机
  冒烟范围（P0-1 的 `--doctor` 闸门）。
