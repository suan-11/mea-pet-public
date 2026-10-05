# VITS「合流后读数」（社区提案 `vits-windows-postmerge-readouts`）Windows 侧收尾总结

**面向**：Neko_mea（转交人 → 提案作者 pid-320592）
**提案原件**：`vits-windows-postmerge-readouts.md`（kind=proposal，status=community，作者 pid-320592）
**本次拉取**：`origin/main` @ `6c720b6`（`Merge pull request #83 from FlexiAtom/main`），本地 `main` 已 ff 到同一点，工作树干净
**本机**：Windows 10.0.19045 / PyInstaller 6.21.0 / miniconda3 Python 3.13.13 / torch 2.12.1+cpu / `vits_ft` = Python 3.8.20 + torch 2.1.2+cu121
**写件日期**：2026-10-05

---

## 0. 一句话结论

**R1 = True**：打包形态里 `find_spec('torch')` 命中随包 torch，提案担心的「新加的 `torch=` 键把打包版进程内那条路
被自己的健康检查关掉」**不成立**。按你给的清算规则（`R1=True ⇒ 谎报的进程内那一半闭合，本件结束`），**本件可以结束**。

其余四条：

| | 你要的读数 | 真值 | 一句话 |
|---|---|---|---|
| **R1** | 健康行逐字 | `core=True model=True config=True torch=True mode=inprocess reason=explicit_inprocess` | 命中，不误伤 |
| **R2** | 同一次运行里进程内出不出声 | **不出声**（`speak()` → `(None, "")`），**但不是判据报红** | 卡在 torch 的 `c10.dll` 冻结进程内初始化失败（WinError 1114）——你代码里已写明的**残余盲区**那一格 |
| **R3** | 打包版会不会走到 pet-exe 那行警告 | **那行字本身会打**（冻结进程实测打出）；**现行调用图走不到它** | 上游 `_check_torch` 用**同一个谓词**先把它挡掉了 |
| **R4** | 探针耗时 + 三档可达性 | **6.91 / 6.98 / 7.07 s**，全部落 `ok`；`missing` / `unknown` 两档在 Windows 都实测可达 | 90 s 闸余量充足（约 8%） |
| **R5** | `vits_infer.py --check-deps` 的 rc/stdout/耗时 | 逐字命令 **rc=2**；探针实际用的 argv 形状 **rc=0，`OK:deps_loaded`，8.67 s** | rc=2 **不是** argparse 不认开关，是 `-t/--text` 必填 |

---

## 1. 先做的两件事：拉取 + 合流核对

```
$ git fetch origin --prune            # 期间 GitHub 多次连接被重置，第 2 次尝试成功
$ git log --oneline -3 origin/main
6c720b6 Merge pull request #83 from FlexiAtom/main
863b1a6 docs(tts): 来源署名补一条别名——Neko_mea 与 suan-11 同一人
d565980 Merge remote-tracking branch 'origin/main'
$ git merge --ff-only origin/main     # b19badd..6c720b6，ff 成功（24 文件 / +1218 −927）
```

你在提案里点名的两处判据，我在合流点逐字复核，数字与你的相同：

```
$ git show origin/main:meapet/tts/service.py | grep -c module_present   ⇒ 2
$ git show origin/main:meapet/tts/common.py  | grep -c probe_vits_deps  ⇒ 1
```

即：**上一轮「取不到读数」的结构性原因确实消掉了**，探针（`5b1122e`）与判据（`d568f89`）都在 `main` 上，
`vits_infer.py --check-deps`、`tests/test_vits_deps_probe.py`、`tests/test_tts_env_paths.py` 也随包到位。

---

## 2. 口径先说清：哪些是「打包版真身」，哪些是「同 spec 的冻结读数入口」

这是本件最需要你判读的一节，因为**这台机器上没有现成的发布包**（`dist/` 不存在、盘上也搜不到 `MeaPet.exe`）。
所以我做了两件事，读数分两栏记：

| 栏 | 是什么 | 有哪几条读数 |
|---|---|---|
| **A. 产品真身** | 按 `scripts/build_windows.ps1` + `MeaPet.spec` 在本机现打的 onedir 包（`dist/MeaPet/MeaPet.exe`，1.59 GB，`_internal/torch` 360 MB/2221 文件），**入口就是 `pet.py`** | 启动后的选路行；`_internal/logs/*.log` |
| **B. 同 spec 冻结读数入口** | 同一份 `datas` / `hiddenimports` / `hookspath` / `excludes` 的 PyInstaller 冻结件，**只把入口换成读数脚本**（`dist-readout/MeaPetReadout`） | R1 健康行、`import torch` 结果、进程内合成结果、R3 的向导分支 |

**为什么必须有 B**：R1/R2 那一格要「直接调 `health_check()`」，而产品真身是 GUI 桌宠，
我没有可用的触发面（见 §10：本机打出来的包里 `agent_control` 的 MCP 端口没起来，
`meapet.say` 这条无头触发路走不通；Windows UIAutomation 也看不到 Qt 控件树，点不了界面）。
B 与 A 的**唯一差别是入口脚本**：torch 的收集方式、`sys._MEIPASS` 布局、`find_spec` 的可见面完全同形。
A 栏拿到的两条（选路行、进程内被选中）与 B 栏互相印证。

---

## 3. R1 / R2 细读

### 3.1 R1 · 健康行逐字（冻结件 B，前置配置 `engine=vits` / `vits_inprocess=true` / `vits_python=""`）

```
route = mode=inprocess reason=explicit_inprocess
MeaTTS (VITS) | engine=vits | mode=inprocess reason=explicit_inprocess | speaker=Mea | model=G_latest.pth | config=finetune_speaker.json
Health (vits): core=True model=True config=True torch=True mode=inprocess reason=explicit_inprocess
health_check() -> True  (0.001s)
```

同一进程里把判据本身也抄下来了：

```
module_present('torch')      = True
find_spec('torch').origin    = ...\MeaPetReadout\_internal\torch\__init__.py
find_spec('torch').loader    = PyiFrozenLoader
```

**这条就是你要的解释权**：打包版里 torch 是**落盘**在 `_internal/torch` 的（不是只进 PYZ），
`_MEIPASS` 又在 `sys.path` 上，所以 `find_spec` 命中的是**同一份** torch，不是「寻得到但引擎其实走别的路」。
你按源码推的「`_import_runtime` 不为 torch 本体动 `sys.path`，那只是推论」——**推论成立，且这里把它证了**。

产品真身 A 栏的对应读数（`dist/MeaPet/_internal/logs/tts.log`，启动 25 s 内）：

```
[INFO] [tts] MeaTTS (VITS) | engine=vits | mode=inprocess reason=explicit_inprocess | speaker=Mea | model=G_latest.pth | config=finetune_speaker.json
```

即：**打包版确实选进了进程内那一格**，且健康检查在这一格报绿。

### 3.2 R2 · 同一次运行里出不出声

**不出声。** 但失败点不是健康检查，是 `import torch` 本身：

```
[import torch] 抛异常 OSError: [WinError 1114] 动态链接库(DLL)初始化例程失败。
                Error loading "...\_internal\torch\lib\c10.dll" or one of its dependencies.   (0.82 s)

tts.speak("こんにちは、テストです。", "neutral", "", "jp") -> (None, '')          (1.53 s)
vits_runtime.synthesize_vits(...) -> OSError（同上，未产出 wav）
```

引擎侧那一支**照你代码里写的那样出声了**（`logs/tts.log`，ERROR 级）：

```
[INFO]  [tts] VITS route: mode=inprocess reason=explicit_inprocess
[INFO]  [tts] VITS inference (in-process)...
[ERROR] [tts] VITS in-process exception: OSError: [WinError 1114] 动态链接库(DLL)初始化例程失败。
              Error loading "...\_internal\torch\lib\c10.dll" or one of its dependencies.
```

所以这一格的形状是：

- **没有**「健康检查报红、把本来能用的路关掉」——健康检查是绿的，`health_check() -> True`；
- **是**你注释里预留的那半格：`find_spec` 只能证明「寻得到」，证不了「加载得起来」；
- 出声面是够的（ERROR 行 + `speak()` 返回 `(None, "")` 让上层回退预制语音），**但健康检查分不出这一格**——这是设计上就认了的，不是回归。

### 3.3 这条 DLL 失败是什么性质（我查到的边界，供你判它算不算本件的问题）

| 检查 | 结果 |
|---|---|
| `ctypes.WinDLL(包内 c10.dll)` 在**普通（非冻结）**解释器里 | **成功**（先装包内 CRT 14.44 / 先装系统 CRT 14.51 / 什么都不装，三种都成功） |
| 同一条在**冻结进程**里 | 失败：`PyInstallerImportError: Failed to load dynlib/dll ... c10.dll`，根因同为 WinError 1114 |
| 换**干净 PATH**（只留 `system32;Windows;Wbem`）重跑冻结件 | 同样失败 ⇒ **不是**我这边 conda PATH 污染出来的 |
| `torch\lib` 内容 | 9 个文件全在（c10/torch_cpu/torch_python/uv/libiomp5md…），**没缺 DLL**（源目录 24 项里少的是 `.lib` 与 `libshm*`） |
| 包内 MSVC 运行时副本 | 至少 6 份不同版本：`_internal\msvcp140.dll` 14.44、`numpy.libs`/`pandas.libs` 14.40、`llvmlite.libs` 14.44、`sklearn\.libs` 14.51、**`PyQt5\Qt5\bin\MSVCP140.dll` 14.26（2018）**；系统是 14.51 |

**我没有证到根因。** 现象是「同一个 DLL，冻结进程里 DllMain 起不来，非冻结进程里没事」，
包内那堆版本混杂的 CRT 是**可疑面**（尤其 `PyQt5\Qt5\bin` 那份 14.26 在冻结 PATH 上），但这只是线索，不是结论。

**口径提醒**：这份失败是**我这台机器、这个构建环境（conda torch 2.12.1+cpu + PyInstaller 6.21）打出来的包件**的属性。
正式发布包如果随包的是别的 torch（或根本不随包），这一格会变。**机制层面的结论（`find_spec` 命中随包 torch）与它无关**，
但「打包版进程内到底能不能真出声」这条，建议在**发布构建环境**里再复一次。

---

## 4. R3 细读：pet-exe 那行字，到不到得了

你问两个问题：「真的会走到」+「会不会被别的路径误触发」。答案是：**会打，但走不到；也没被误触发。**

### 4.1 那行字本身会打（冻结进程实测）

在真冻结进程里，把 pet exe 直接交给 `_ensure_vits_deps`：

```
is_pet_executable(sys.executable) = True
_path_is_pet_exe(sys.executable)  = True
probe_vits_deps(pet_exe, script)  = ('unknown', 'no external python')      # 探针自己的闸也一致
[wizard.log]   ⚠ 打包版中无法检查 VITS 依赖（pet exe 不是 Python 解释器）      # ← 逐字，含两个前导空格
```

### 4.2 但真实调用图走不到它

`_ensure_vits_deps(py_exe, ...)` 全仓 5 个调用点（`wizard/page_tts_vits.py`）：

| 行 | 传进去的 `py_exe` | 到不到得了 pet-exe 分支 |
|---|---|---|
| 396 | `_sys.executable`（**冻结版就是 pet exe**） | ❌ **到不了**：它是 `if ver_ok:` 里面的一行，而 `ver_ok` 来自 `_check_torch(_sys.executable)`；`_check_torch` 开头就是 `if _path_is_pet_exe(py_exe): return False, "frozen"`（同一个谓词） |
| 412 | `_python\python.exe`（盘上真解释器） | ❌ |
| 446 | conda `vits_ft`（盘上真解释器） | ❌ |
| 458 | `vits_env\Scripts\python.exe`（盘上真解释器） | ❌ |
| 576 | `_on_vits_env_done(result, …)`，`result` ∈ {`_python`, 新建 venv 的 python} | ❌（且 `_master_py` 落到 pet exe 时是「venv 创建失败」，不会走到探针） |

**守卫与分支用的是同一个谓词**（`_path_is_pet_exe` → `is_pet_executable`），所以「谓词为真」时，
上游一定先返回 `False, "frozen"`；谓词为假时，分支那句也不会打。两边锁死。

### 4.3 真实 `_setup_vits_env()` 在冻结进程里的实测（确认框以 monkeypatch 代点）

```
[wizard.log] ✓ 使用 C:\Users\JOYCEPC\.conda\envs\vits_ft\python.exe
[spy t+3.742s] _ensure_vits_deps(py_exe='...\\envs\\vits_ft\\python.exe') pet_exe=False
[wizard.log] ✓ VITS 推理依赖就绪
--- 结束：_ensure_vits_deps 收到 [('...vits_ft\\python.exe', 3.742)] ---
```

两个可判读的点：

1. **pet exe 一次都没被交到探针入口**（spy 全程只收到 conda 那个解释器）——走的是 1️⃣ 档（本机恰好有 `vits_ft`）。
2. **计时判据**：从进入 `_setup_vits_env` 到探针调用共 **3.742 s**，而 `_check_torch(vits_ft)` 这一次子进程
   `import torch` 本机实测就要 ~3 s ⇒ 0️⃣ 档那次 `_check_torch(pet exe)` 是**秒回**的（走守卫），
   不是「拿 pet exe 当 Python 跑、等 15 s 超时」那条路。

顺带：这一步也是**在冻结件里跑通了 R4 的探针**（`✓ VITS 推理依赖就绪`，内部走 `probe_vits_deps`）。

### 4.4 用户可见后果（这条要你裁）

打包版里，那行「打包版中无法检查 VITS 依赖」**永远不会出现**，用户看到的是另一套路径：

- 有 conda `vits_ft` / `_python` / `vits_env` ⇒ 直接命中并探针（本机就是这样）；
- 都没有 ⇒ 落到 3️⃣ 建 venv，`_master_py` 取 `shutil.which("python")`，
  取不到就**回落到 pet exe** ⇒ `python -m venv` 必然失败 ⇒ 状态条是 **`配置失败: venv 创建失败`**，
  而**不是**那句更能解释问题的「打包版无法检测 VITS 依赖」。

也就是说：**这行警告现在是防御性死代码**。若你希望它在打包版真出现，得把它挪到
`_check_torch` 的守卫**之前**（或用 `_check_torch` 的 `"frozen"` 返回值去触发它）；
不过 4.2 的表说明，改了以后用户看到的就是「本机既没 vits_ft 也没 _python 也没系统 python」那一格——
要不要这么报，是产品口径，我和你都无权替维护者定。

---

## 5. R4 细读：探针耗时与三档可达性

### 5.1 耗时（Windows 侧，向导同形调用：`probe_vits_deps(vits_ft_python, meapet/tools/vits_infer.py)`）

| 第几次 | 结论 | 耗时 |
|---|---|---|
| 1 | `ok` / `deps importable` | **7.07 s** |
| 2 | `ok` / `deps importable` | **6.98 s** |
| 3 | `ok` / `deps importable` | **6.91 s** |

对照你 Linux 侧 12–20 s、闸 90 s：**Windows 侧快一倍以上，闸的余量约 8%**（你没有 3.8/Windows 这一格，这就是）。
另：R5 那次直接从命令行跑同一条 import 面是 8.67 s（含解释器启动），量级一致。

### 5.2 三档可达性（都用真解释器/真脚本，不 patch 判据）

| 档 | 现场 | 结论 | 耗时 |
|---|---|---|---|
| `ok` | `vits_ft`（有 torch） | `ok` / `deps importable` | 6.91–7.07 s |
| `missing` | 新建空 venv（真解释器、**真没 torch**） | `missing` / `⚠ pkg_resources 不可用 (ModuleNotFoundError)；若合成失败请: pip install 'setuptools==69.5.1'` | 0.15 s |
| `unknown` | 没解释器（空串） | `unknown` / `no external python` | ~0 s |
| `unknown` | 脚本不在盘上 | `unknown` / `infer script missing` | ~0 s |
| `unknown` | 解释器路径不存在 | `unknown` / `FileNotFoundError: [WinError 2] …` | ~0 s |
| `unknown` | 装死脚本 + **把 90 s 闸临时压到 1 s** | `unknown` / `probe timeout (1s)` | 1.01 s |
| `unknown` | `sys.exit(3)`（rc≠0 但不是 ImportError） | `unknown` / `rc=3 ` | 0.11 s |

**三档在 Windows 都可达，`unknown` 这一档的五种入口也都落得住**（不会把「探针自己坏了」说成「用户环境坏了」）。

### 5.3 顺手抓到的一条：`missing` 档的 `detail` 会抽错行（可复现）

上面 `missing` 那格，**真因是缺 torch**，stderr 末尾逐字是：

```
  ✓ 使用项目内置日语词典
  ⚠ pkg_resources 不可用 (ModuleNotFoundError)；若合成失败请: pip install 'setuptools==69.5.1'
Traceback (most recent call last):
  File "...\meapet\tools\vits_infer.py", line 257, in <module>
    _load_torch_stack()
  File "...\meapet\tools\vits_infer.py", line 100, in _load_torch_stack
    import torch
ModuleNotFoundError: No module named 'torch'
```

而 `probe_vits_deps` 取 detail 用的是 `next(ln for ln in stderr.splitlines() if "Error" in ln)`
——**第一行**含 `Error` 的，于是抓到的是那句**关于 pkg_resources 的劝告**，
不是真正的 `ModuleNotFoundError: No module named 'torch'`。
向导状态条因此会写「VITS 依赖不完整：⚠ pkg_resources 不可用…」，把用户指向 `setuptools`。
一行就能改对（取**最后**一条 `ModuleNotFoundError: No module named 'X'`，或按 stderr 尾部倒序找）。
**我不改码，只报读数**，这条交你判。

---

## 6. R5 细读：两种 argv 形状，rc=2 的真正原因

你给的逐字命令与探针实际跑的 argv 不是同一个形状，结果也不同：

| 形状 | argv | rc | stdout 首行 | 耗时 |
|---|---|---|---|---|
| **提案逐字** | `--check-deps` | **2** | （空） | 1.13 s |
| **探针实际** | `--check-deps --text probe --output NUL` | **0** | `OK:deps_loaded` | 8.67 s |

逐字那条的 stderr 末尾（关键在这两行）：

```
usage: vits_infer.py [-h] -t TEXT [-o OUTPUT] [-s SPEAKER]
                     ...
                     [--length_scale LENGTH_SCALE] [--warmup] [--check-deps]
                     [--model MODEL] [--config CONFIG]
vits_infer.py: error: the following arguments are required: -t/--text
```

**`--check-deps` 就在 usage 里**——argparse 认识它。rc=2 是因为 `-t/--text` 是 `required=True`，
而逐字命令没给。**所以「探针与脚本契约断了」这条判据（rc=2 = argparse 不认开关）不成立**：
探针在 `probe_vits_deps` 里显式补了 `--text probe --output os.devnull`（Windows 上就是 `NUL`），
契约在**探针 → 脚本**这条路上是通的，`tests/test_vits_deps_probe.py::test_infer_script_accepts_the_check_deps_flag` 也只覆盖这个形状。

要留给你裁的是另一件事：**脚本自己不自洽**——help 写着「只验证推理依赖能否 import，不读权重」，
但用户照 help 敲 `--check-deps` 单独一条，拿到的是 rc=2 的「缺 `-t/--text`」。
这算不算你预留的那类 BUG 件，我按「逐字命令不可用」如实报，不替你定性。
（另：三种形状都会在 stderr 打一条 `pkg_resources is deprecated` 的 DeprecationWarning，不影响判据。）

---

## 7. 按提案清算规则的落点

| 你的规则 | 本机读数 | 落点 |
|---|---|---|
| `R1=True` ⇒ 谎报的进程内那一半闭合，本件结束 | **R1=True** | ✅ **本件结束** |
| `R1=False` 且 R2 不出声 ⇒ 报红成立 | 未出现 | — |
| `R1=False` 而 R2 出声 ⇒ 回来改判据（新改动、须重新授权） | 未出现 | — |
| R3–R5 只补 W4 与文案的账 | R3 补上（并发现那行是死代码）；R4/R5 补上 | ✅ |
| `R5` 若 rc=2 另开 BUG 件 | rc=2 出现了，**但不是你预设的原因** | ⏸ **待你裁**（见 §6） |

**你担心的那一格（`find_spec` 在打包版命中与否）现在有真值了：命中。** 判据不必改第二版，
§2 反证表第三行「本行由 Neko_mea 填」的那格，就填 `find_spec` 命中、健康行 `torch=True`。

**但有一格现在浮出来了**：`R1=True` 并不等于「打包版进程内能出声」。本机的包件里
`find_spec=True` + `import torch` 失败（WinError 1114）⇒ 健康检查报绿、合成不出声。
这正是你代码注释里认下的残余盲区；它值不值得换判据（比如打包版把「真 import 一次」挪到后台线程），
**按你 §3 的写法要等这组读数——现在读数有了**：
「后台真 import」这条路在本机的代价是 **0.8–1.0 s 的失败返回**（成功时按 3.9 s 量级），
而且**它真的能分辨出这一格**（`find_spec=True` 但 `import=False`），比 `find_spec` 多一层信息。

---

## 8. 环境与边界（复现前请先读这一节）

1. **本机没有发布包**，A 栏是我按 `scripts/build_windows.ps1` 现打的：`python -m meapet.bootstrap --check all`
   通过后 `python -m PyInstaller --noconfirm MeaPet.spec`（耗时 **579 s**，产物 1.59 GB）。
2. **我动了本机解释器环境**：为过构建闸（以及打开 `agent_control`）往 miniconda3 装了
   `websockets 15.0.1` + `mcp 1.30.0`（走清华源）。这会把本仓的 pytest 基线从上一件的
   「957 passed / 50 failed / 9 skipped」推到 **1028 passed / 8 failed / 9 skipped**
   （差的 42 条正是原先被「websockets 未装」挡掉的那些），源码与 `linux_requirements.txt` 未动。余下 8 条仍是环境门槛
   （Wayland/click-through 3 条、Athena 状态根 4 条、`test_tts_env_paths.py::test_gsv_python_candidates_cover_posix_layouts`
   1 条 POSIX 布局），逐条见 §9.5。
3. **R2 的 DLL 失败是构建环境的属性**，不是合流引入的（判据/探针都没碰 DLL 装载面）；
   发布构建环境下请复一次（一条命令见 §9.4）。
4. B 栏是**同 spec 的冻结读数入口**（只换入口脚本），已在 §2 交代；A/B 两栏的选路行逐字相同。
5. 提案三条约束的遵守情况：**未改任何产品代码**（本件只加一份 docs 文档）；
   **未动远端历史**；**未引入新外联**（GitHub fetch 是拉取动作本身；`pip install` 走的是仓库既有的清华源配置）。

---

## 9. 复跑命令

```bash
# 9.1 合流核对（与提案同形）
git fetch origin --prune && git merge --ff-only origin/main
git show origin/main:meapet/tts/service.py | grep -c module_present    # 2
git show origin/main:meapet/tts/common.py  | grep -c probe_vits_deps   # 1

# 9.2 R4：探针耗时与三档（在 app 的解释器里跑，向导同形调用）
python - <<'PY'
import sys, time; sys.path.insert(0, r"D:\githb\mea-pet-public")
from meapet.tts.common import probe_vits_deps
py = r"C:\Users\JOYCEPC\.conda\envs\vits_ft\python.exe"
script = r"D:\githb\mea-pet-public\meapet\tools\vits_infer.py"
for i in range(3):
    t0 = time.perf_counter(); print(probe_vits_deps(py, script), f"{time.perf_counter()-t0:.2f}s")
PY

# 9.3 R5：两种 argv 形状
"C:\Users\JOYCEPC\.conda\envs\vits_ft\python.exe" meapet\tools\vits_infer.py --check-deps                    # rc=2
"C:\Users\JOYCEPC\.conda\envs\vits_ft\python.exe" meapet\tools\vits_infer.py --check-deps --text probe --output NUL   # rc=0, OK:deps_loaded

# 9.4 R1/R2：本机包件
python -m meapet.bootstrap --check all
python -m PyInstaller --noconfirm MeaPet.spec                      # 本机 579 s
# 前置配置写进 dist\MeaPet\_internal\config.json：
#   tts.engine="vits" / tts.vits_inprocess=true / tts.vits_python=""
# 再启动 dist\MeaPet\MeaPet.exe；健康行落在 _internal\logs\tts.log
```

### 9.5 CONTRIBUTING 三条闸门（本件跑过，与本轮合流对照）

```
python -m ruff check meapet wizard scripts tests   ⇒ All checks passed!        (exit 0)
python -m compileall -q meapet wizard              ⇒ exit 0
python -m pytest -q                                ⇒ 8 failed, 1028 passed, 9 skipped, 218 subtests passed (42.90s)
```

余下 8 条失败逐条（全部环境门槛，与本次读数无关）：

```
FAILED tests/test_app_standby.py::BackendAutoDetectionTests::test_wayland_via_env
FAILED tests/test_click_through.py::PlatformBackendNameTests::test_env_fallback_when_no_qt_app
FAILED tests/test_click_through.py::PlatformBackendNameTests::test_qt_plugin_beats_session_env
FAILED tests/test_layer_bridge_abi.py::test_spec_7_1_table_is_the_expected_cardinality
FAILED tests/test_layer_bridge_abi.py::test_build_script_gate_agrees_with_spec
FAILED tests/test_layer_bridge_abi.py::test_provenance_gate_rejects_a_non_rust_export_set
FAILED tests/test_layer_bridge_abi.py::test_python_binding_surface_matches_spec
FAILED tests/test_tts_env_paths.py::test_gsv_python_candidates_cover_posix_layouts
```

前 7 条是 Wayland / Linux 后端名 / Athena 状态根缺失。**最后一条值得你看一眼**：
`tests/test_tts_env_paths.py` 是本轮合流新进的（`create mode 100644`），
它在 Windows 上失败于断言的候选表是 POSIX 形状（`.../runtime/bin/python`）——
Linux CI 上应当绿，Windows 本机必红。若本仓有 Windows CI，这条要在用例里按平台分叉；没有就当我没说。

---

## 10. 顺手抓到的一条（与 R1–R5 无关，但挡住了本件的一条路）

产品真身 A 栏启动后（我写的 `config.json` 里 `agent_control.enabled=true`、`port=8765`、token 38 字符）：

```
[control_bridge] [control] Companion MCP 已启动: endpoint=http://127.0.0.1:8765/mcp agent_ip=127.0.0.1
$ Test-NetConnection 127.0.0.1 -Port 8765   ⇒ False        # 日志说启动了，实际没监听
```

`CompanionMcpRuntime.start()` 把 `_serve()` 丢进 `meapet.async_runtime` 的后台 loop，
**`_future` 没人读**，所以 uvicorn 侧的任何异常都不会落到任何日志文件里——这条可观测性缺口是真的，
但**根因我没证到**（冻结件里的复现探针写短了 token，被 `auth_token must contain at least 32 characters` 挡在构造阶段，没跑成），
故只作侧记，不计入 R1–R5。它导致的直接后果是：**产品真身的 `meapet.say` 无头触发路走不通**，
R1/R2 只好用 §2 的 B 栏取（见 §2 的口径说明）。

---

## 11. 待办 / 留给你与维护者的三条

1. **R1/R2 的发布环境复现**（一条命令，§9.4）。本机包件里 `import torch` 失败在 `c10.dll`（WinError 1114），
   可疑面是包内 6 份版本混杂的 MSVC 运行时（含 `PyQt5\Qt5\bin` 那份 14.26）；我没证到根因，别把它当成结论。
2. **R3 那行警告的取舍**：现在到不了（§4.2 表 + §4.3 实测）。要它出现就得挪位置，代价是打包版无 env 时
   用户看到的是「无法检测」而不是「创建 venv 失败」——两种都如实，请裁。
3. **§5.3 的 `detail` 抽错行**与 **§6 的 `--check-deps` 不能单独跑**：两处都是小改，
   但都改了判据面/交付脚本契约，按提案约束①「不必为此插桩改码」我未动，留给下一件。

---

## 12. 相关文档

- [`docs/tts-vits-routing.zh-CN.md`](tts-vits-routing.zh-CN.md) —— 判据/配置/日志/读数的唯一说明来源（本轮合流后已含 Linux 侧读数）。
- [`docs/vits-windows-knobs-divergence-closeout.zh-CN.md`](vits-windows-knobs-divergence-closeout.zh-CN.md) —— 上一件（旋钮口径分歧）的 Windows 收尾，
  本件的 W4 缺口（探针耗时）在那份文档 §5 里挂着，本件 §5 已补齐。
- [`docs/troubleshooting.zh-CN.md`](troubleshooting.zh-CN.md) —— 用户视角的 VITS 排查。
- 读数脚本（**不在仓库内**，放在 `%TEMP%\meapet-readouts\`）：`readout_entry.py`、`MeaPetReadout.spec`、
  `r4_probe_timing.py`、`r4b_tiers.py`、`r5_check_deps.py`、`mcp_say.py`、`make_config.py`；
  产物 `readout_report.txt`（冻结件内）、`r4_result.json`、`r4b_result.json`、`r5_result.json`。
  需要的话我下轮把它们收进 `scripts/` 并配一条验收命令。
