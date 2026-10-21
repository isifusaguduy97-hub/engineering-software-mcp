# Engineering Software MCP

Windows 本地 MCP 服务，把自然语言工程任务连接到 **COMSOL、Autodesk Fusion 和 LabVIEW**。使用官方软件接口；软件和有效许可证由使用者自行准备。

项目仓库：**https://github.com/isifusaguduy97-hub/engineering-software-mcp**。

**这是同时提供三个软件工具的一个 MCP 服务。** PDF 用 COMSOL 作为详细模拟实例，同时讲解 Fusion 与 LabVIEW 的连接。下载时选择仓库页面的 **Code → Download ZIP**，解压完整项目后运行 Setup.cmd；本版本不要求发布到 PyPI。

验证记录保存了制作时的本地验证状态，仓库当前状态以 GitHub 页面为准。SHA256SUMS.txt 用于核对源码 ZIP 解压后的文件；Git 克隆时若另行转换换行，可能改变部分文本文件的字节校验值。

## 新手从这里开始

打开 docs 下的 PDF：

- **01_下载MCP到完成COMSOL模拟.pdf**：从下载、安装、接入 Codex，到导热杆模拟。
- **02_上传到GitHub操作指南.pdf**：创建仓库、上传项目、分享和更新。

安装前准备 Windows、Python 3.12（64 位推荐）、支持本地 STDIO MCP 的 AI 客户端，以及本次使用的软件。安装只需要当前用户权限，首次安装 Python 依赖需要联网。仅使用 COMSOL 时不需要安装 Fusion 或 LabVIEW。

1. 解压完整项目到可写目录，例如 `C:\EngineeringMCP`，确认此目录直接包含 `Setup.cmd`、`requirements.txt`、`engineering_mcp`。
2. 双击 **Setup.cmd**。它在项目内建立 `.venv`、安装依赖、检测软件，生成 **config.local.json** 和 **codex-mcp.toml**。
3. 在 Codex 的设置中添加 MCP 服务器，选择 **STDIO**，使用生成文件中的 command、args 和 env。也可把该 TOML 小节加入自己的 Codex 配置。安装器不会自动修改全局设置。
4. 重启该 MCP 服务器/客户端，发出“请调用 engineering_status，检查当前可用的软件”。
5. 复制下面的导热杆示例，让 AI 提交任务、跟踪 job_id，并读取 verification.json。

未检测到 COMSOL 时，在项目目录打开终端，按实际安装位置运行：

```bat
Setup.cmd --comsol-root "E:\COMSOL64\Multiphysics"
```

MCP 启动进程以 JSON 协议通信，直接双击 `Start_MCP.cmd` 后等待输入属于正常状态；这不能替代客户端接入验证。

## 三个软件分别怎么启动

### COMSOL

配置正确即可由批处理启动，无须先开图形界面。COMSOL 需要可写的配置、缓存和临时目录，runner 均放在配置 workspace/work 下，不修改安装目录或许可证。批处理方法执行可能需要本机通信权限；限制环境中请使用该客户端正常的权限流程。

### Autodesk Fusion

在项目目录终端执行：

```bat
.venv\Scripts\python.exe tools\install_fusion_addin.py
```

然后正常打开 Fusion，按 **Shift+S**，在附加模块中运行 **EngineeringMCPFusionBridge**，保持软件打开。该独立插件不替换已有 CodexFusionBridge；两者的工作目录应按自己的配置确定。

### LabVIEW（可选）

```bat
.venv\Scripts\python.exe -m pip install pywin32==312
.venv\Scripts\python.exe tools\prepare_labview.py
```

然后在资源管理器普通双击 **Start_LabVIEW.cmd**，看到 `LabVIEW connected` 后保持窗口打开。准备工具从本机安装的类型库生成 typed wrappers；不修改全局 COM 注册、系统安全设置或 NI 库。调用已有 VI 与创建新图形框图是不同能力。

## 可直接使用的任务

### COMSOL 导热杆

> 请先检查 COMSOL，再调用 comsol_rod_heat：杆长100 mm，导热系数200 W/(m·K)，左端100℃、右端20℃，横截面恒定、侧面绝热、无内部热源，一维稳态，21个等距点、100个网格单元。获得job_id后查询engineering_job_status直到完成，读取verification.json核对中点60℃和解析解误差，提供MPH、CSV和温度曲线路径。

默认结果：`rod.mph`、`temperature_21_points.csv`、`temperature_profile.svg`、`verification.json`。这个数学接口示例不要求 MATLAB。截面积未指定时不计算总热功率。

### Fusion 安装板

> 请检查并ping Fusion，然后调用fusion_mounting_plate，制作60×40×5 mm安装板，四个孔心距相邻直线外边各10 mm，孔径5 mm贯穿，四条外侧竖直棱边R3。跟踪job_id到完成，核对尺寸、孔位和建模历史，给出F3D、STEP、mm单位STL及等轴测图。

### LabVIEW 均值

> 请检查并ping LabVIEW，调用labview_mean，输入[10,20,30,40,50]。跟踪job_id，核对均值30、错误码0，提供输入CSV与实际运行记录。

## 工具一览

| 工具 | 用途 |
| --- | --- |
| engineering_status | 配置、安装和心跳状态；不等于许可/求解验证 |
| engineering_ping | 实际握手；COMSOL 只测试启动器 |
| comsol_rod_heat | 参数化一维稳态导热杆，原生求解与解析检查 |
| comsol_solve_mph | 运行 workspace 内可信 MPH 的研究，输出新 MPH |
| fusion_mounting_plate | 参数化带孔安装板、导出和几何检查 |
| labview_mean | 通过已有 Mean (DBL).vi 计算均值 |
| engineering_job_status | 查询 queued / running / completed / failed |
| engineering_list_artifacts | 列出该任务的导出、源文件与日志 |
| engineering_read_text | 读取该任务内文本结果与日志 |
| engineering_submit_script | 高级 Java/Python 工程脚本；默认关闭 |

任务输出位于 **配置 workspace/outputs/mcp-runs/job_id**。每个任务使用独立目录，保留原文件。超时、客户端退出或停止等待并不等于已取消软件中的任务；先查状态和日志，再决定下一步，避免重复提交。

## 检查与测试

```bat
Check.cmd
.venv\Scripts\python.exe tools\self_test.py
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

可选的 GitHub Actions 配置放在 `examples/github-actions-test.yml`，当前仓库未启用自动工作流。如需启用，可自行将该文件放入 `.github/workflows/test.yml` 后提交；使用 OAuth 上传时需要 GitHub 的 `workflow` 权限。示例只运行配置、单元测试和 MCP 协议检查，不调用商业软件。

前两步分别检查本地条件、真实 MCP 初始化与工具调用。默认不会启动建模。明确要运行软件实战时再执行：

```bat
.venv\Scripts\python.exe tools\self_test.py --demo comsol
.venv\Scripts\python.exe tools\self_test.py --demo fusion
.venv\Scripts\python.exe tools\self_test.py --demo labview
```

默认是同步 STDIO 收发，使用官方 `mcp==1.30.0` SDK 的工具 schema、参数验证与结果模型。没有 HTTP 监听端口，也不开放远程控制。在部分 Windows 限制环境下，Python asyncio 的内部 socketpair 会卡住；默认模式不依赖它。需要对照官方异步传输时，服务端可选 `--sdk-transport`，测试端可选 `--official-sdk-client`。

## 能力范围

- 基础软件流程曾在 COMSOL 6.4.0.429、Fusion 2705.1.30、LabVIEW 2025 Q1 32 位25.1f0上验证；本版本 MCP 的实际测试结果见 `docs/验证记录.json`。
- COMSOL 专用物理接口和 CAD 导入仍取决于许可及版本；保存 MPH 不能单独证明收敛或物理正确。
- LabVIEW 当前支持现有 VI 的调用；没有声称已能生成任意前面板/框图，也未验证真实硬件采集、FPGA 或实时目标。
- 默认仅提供固定的结构化操作。需要更通用建模时，由操作者审查权限后将本地配置的 `enable_scripts` 改为 `true`，AI 才可提交 Java/Python 工程代码。
- 高级代码以运行用户的权限执行，**不是隔离沙箱**。代码执行只用于已授权工程自动化，不用于系统安全修改、许可证修改、无关 shell 操作或未授权设备/云操作。

## 发布内容与本地文件

可上传项目源码、模板、PDF、测试和许可证。不要上传 `.venv`、`work`、`outputs`、`config.local.json`、`codex-mcp.toml`、自动生成的 `labview_bindings.py` 或任何密钥/许可证。`.gitignore` 对 Git 提交生效；网页拖拽上传时仍应使用干净的发行源码包。

该 MIT 许可仅覆盖本项目自行编写的代码。COMSOL、Autodesk Fusion、LabVIEW 与各依赖的商标、软件和许可归各自权利人所有。本项目不是三家厂商的官方 MCP 插件。

## 官方资料

- [MCP 架构](https://modelcontextprotocol.io/docs/learn/architecture)
- [官方 Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)
- [OpenAI Docs 的 MCP 接入说明](https://developers.openai.com/codex/mcp)
- [COMSOL API](https://www.comsol.com/support/learning-center/article/overview-of-the-comsol-api-107912)
- [GitHub 创建仓库](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository)
