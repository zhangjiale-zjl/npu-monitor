# 本地 NPU 节点监控

通过 SSH 密钥或密码认证定时执行 `npu-smi info`，在本机浏览器查看多个节点的 NPU 状态。运行依赖 **Python 3.10+、系统 OpenSSH 客户端和浏览器**，无需 pip、npm 或公网服务。Windows 密码登录的本地辅助程序从仓库中的源码自动生成，使用系统自带的 .NET Framework 编译器。

看板仅监听 `127.0.0.1`，网页资源全部随工具提供。采集通过 SSH 连接指定节点，因此本机需能访问这些节点的 SSH 端口。支持 Windows、Linux 和 macOS；远端需提供 POSIX `sh` 和 `npu-smi`。

## 目录

`src/python` 仅存放 Python 程序源码，启动脚本、网页、配置、辅助资源和测试位于仓库根目录下的对应位置。

```text
npu-monitor/
├── start.cmd
├── start.ps1
├── README.md
├── LICENSE
├── nodes.example.json
├── helpers/
├── static/
├── tests/
└── src/python/
    ├── monitor.py
    ├── npu_parser.py
    ├── npu_platform.py
    ├── password_auth.py
    └── askpass.py
```

节点配置默认保存在仓库根目录的 `nodes.json`。用户配置、截图、采集结果和生成的密码辅助程序均忽略，不进入 Git 仓库。

## 启动

首次启动显示空看板，可点击“＋ 添加节点”填写自己的连接信息。也可将 `nodes.example.json` 复制为 `nodes.json` 后修改；示例中的地址、用户名和密码均为占位内容。`nodes.json`、采集快照、截图和本机生成的辅助程序不纳入版本控制。

Windows 双击 **`start.cmd`**，自动打开本地看板。启动脚本优先查找已安装的 Python，也支持当前 Codex 自带的 Python。保持启动窗口运行，按 **Ctrl+C** 停止采集并关闭 SSH 连接。命令行选项可传给 `start.cmd`，例如 `start.cmd --interval 2 --no-browser`；演示模式使用 `start.cmd -Demo`。

也可在终端启动：

```powershell
python src/python/monitor.py
```

Linux / macOS：

```bash
python3 src/python/monitor.py --config nodes.json
```

浏览器地址：`http://127.0.0.1:8765`。端口被占用时使用 `--port 8766` 或 `--port 0` 自动分配。

## 使用看板

- **添加节点**：点击顶部“＋ 添加节点”，弹窗只展示一个空白节点表单。填写地址 / SSH 别名，选择“密钥登录”或“密码登录”。用户名留空默认 `root`，端口留空默认 `22`；使用其他用户名或端口时明确填写。密钥登录可沿用本机 SSH 配置中的私钥，也可指定私钥路径；密码登录需填写节点密码。可先点击“测试连接”，再点击“添加节点”追加到现有配置；已有节点的连接信息、密码、顺序和默认连接设置继续保留。名称自动生成“环境类型 - IP”；相同连接地址与端口只保留首次配置，保存时提示合并数量。
- **管理 / 编辑节点**：顶部“管理节点”展示全部节点，机型分区默认按名称字母升序，机型内默认按 IP 数值升序，与左侧目录和看板一致。机型标题左侧的滑块可拖动整个分区，键盘聚焦后可用 ↑ / ↓ 调整；保存后看板、左侧目录和管理列表同步使用该机型顺序，刷新和重启后继续保留。新增机型按字母升序追加到已排序机型后；点击“机型按字母排序”恢复默认机型顺序，保存后生效。节点默认收起，仅显示名称和左侧排序滑块；点击名称展开连接信息、测试连接与移除操作。拖动滑块调整同一机型内的节点顺序，键盘聚焦滑块后可用 ↑ / ↓ 调整；点击“保存配置”后，该机型使用手动顺序，其他机型继续按 IP 升序。手动顺序随本机配置保存，重启后继续保留；该机型新增节点按 IP 升序追加在已排序节点后。各机型标题旁的“按 IP 排序”恢复默认顺序，保存后生效。默认私钥与采集超时放在可展开的“默认连接设置”中。管理弹窗内的“添加节点”新增一条展开的表单，放入“待识别机型”分区。每个节点看板上的“编辑”按钮打开管理弹窗并展开目标节点。保存后立即生效，并写入启动时指定的配置文件；采集暂停时，保存配置会保持暂停状态。
- **测试连接**：每个节点配置中点击“测试连接”，使用当前填写的地址、用户名、端口、认证信息、默认私钥和采集超时，验证 SSH 连接并在宿主机执行 `npu-smi info`，无需先保存。结果分别显示 SSH 是否连通、NPU 输出是否可用、Health 异常数量、命令返回码及实际失败原因。密码留空时仅复用相同地址、端口和用户名已保存的密码；修改节点信息后，测试结果清空。测试不保存配置、不修改采集周期或暂停状态、不追加监控历史。连接参数相同时复用监控会话，并在结果中标注“复用监控连接”；该结果验证当前会话的连通性和命令执行。其他连接参数使用临时会话，测试结束后关闭；最多同时测试 4 个节点。演示模式不建立真实连接。
- **监控周期**：输入 1–3600 秒并点击“应用”。周期按每个节点的采集开始时间计算，单节点采集互不重叠；采集耗时超过周期时，完成后尽快开始下一次。
- **立即采集 / 暂停采集 / 继续采集**：控制全部节点。暂停时正在进行的采集可以完成，页面显示最近一次结果。
- **全部节点总览**：页面默认按服务器型号分区，机型按名称字母升序排列，分区内默认按 IP 数值升序排列，例如末段 2 排在 10 前面。IPv4 与 IPv6 分别按地址数值比较，IPv4 排在 IPv6 前面；SSH 别名按采集得到的实际 IP 排序，尚未取得 IP 时按解析地址或别名自然升序，同 IP 按端口升序。各分区直接展示所有节点，每个节点使用紧凑表格，无需选择节点。总览、分区标题和节点摘要分别统计 NPU 卡数与 die 数；分区标题同时显示节点数和采集异常数。型号未识别的节点默认集中显示在页面末尾，也可随其他机型拖动排序。布局根据窗口宽度自动调整列数，1280 像素宽的窗口可并排显示 3 个节点；节点较多时在同一页面滚动查看。
- **机型目录**：左侧列出机器型号和各型号的节点数量，顺序与看板、管理列表保持一致。拖动目录中的机型行或左侧滑块，松开后自动保存到本机配置，并同步到看板与管理列表；键盘聚焦滑块后可用 ↑ / ↓ 调整并自动保存。保存失败时提示错误并恢复顺序；“全部节点”固定在目录顶部。点击型号只展示该型号的节点，顶部统计同步显示当前机型的数据；点击“全部节点”恢复总览。采集更新和目录折叠保持当前筛选，刷新页面默认展示全部节点。目录可收起或固定展开，浏览器记住折叠状态。桌面端可拖动展开目录右侧的分隔线调整宽度，固定展开与悬浮展开均支持；浏览器记住宽度，刷新后继续使用，双击分隔线恢复默认 168 像素。宽度最小 144 像素、最大 420 像素，并根据窗口大小保留监控内容空间；键盘聚焦分隔线后，左右方向键调整宽度，Home / End 调至最小 / 最大。收起后，鼠标在左侧目录区域连续停留满 0.5 秒时悬浮展开，提前移出会取消计时，再次移入重新计时。展开后移出目录自动收起，拖动期间保持展开；悬浮目录覆盖在内容上方，节点布局保持稳定。悬浮目录中的“固定”按钮使目录常驻展开；键盘焦点进入目录时也可展开并选择机型。窄屏或触屏使用点击展开，目录显示在内容上方。
- **每张 NPU 卡各 die**：每行以 `NPU / Die` 标识卡号与卡内 Chip ID，直接显示该 die 的 **Health 原始状态、AICore（%）、HBM（%）、Memory（%）**。A3 单卡双 die 环境的 8 张卡完整展示 16 行，例如 `0/0`、`0/1` 至 `7/0`、`7/1`；卡号相同的两个 die 保留各自指标，卡与卡之间显示分隔线。解析支持单列 `Chip`、双列 `Chip Phy-ID` 和 `Chip Device`，卡数按 NPU ID 统计，不根据 die 数固定除以二。悬停编号可查看 Phy-ID 或 Device 编号、型号、Bus、温度及功耗；功耗 `-` 显示为缺失值。Health 非 OK 的 die 标红，健康异常数量按 die 统计。悬停内存占用率可查看已用 / 总量（GiB、MB）；展开“详细信息 → 内存明细”可逐 die 查看容量。比例未提供且容量已知时显示原始 MB 数值，例如 `0/0`，数据缺失时显示 `—`。未完整解析的行会提示检查原始输出。
- **详细信息**：展开节点底部“详细信息”，查看内存明细、最近 120 次采集的平均 AICore 趋势、进程列表和原始输出。长错误信息在节点主表中压缩展示，完整错误保留在原始输出。进程列表包含 PID、名称、显存和容器 PID。失败采集显示为曲线空缺，历史仅保存在内存，重启后重新累计。

页面每秒读取本地缓存；SSH 采集频率由“监控周期”单独控制。

## 自动命名与服务器型号

工具以本机 `ssh -G` 解析后的 `HostName` 和有效 SSH 端口作为节点身份，同一地址的重复项保留首次配置，用户名、显示名称和认证方式不参与去重。映射到相同地址和端口的 SSH 别名也会合并；同一地址的不同端口作为不同连接入口保留。已有配置中的 `name` 不参与连接或显示，保存时仅保留当前连接参数。

节点名称自动生成 **`A2 - IP`、`A3 - IP` 或 `A5 - IP`**。连接成功后使用 SSH 会话中的服务器 IP；未取得 IP 时显示 SSH 配置解析后的地址。型号依据 `npu-smi info` 的芯片名称及以下只读查询确定，NPU ID / Chip ID 取当前指标中的实际设备编号：

```bash
# A2 / A3：读取 Chip Name，A3 还需结合 NPU Name
npu-smi info -t board -i <NPU_ID> -c <CHIP_ID>

# Ascend 950 系列：设备级查询
npu-smi info -t board -i <NPU_ID>

# 服务器型号：用作页面分区标题
dmidecode -s system-product-name
```

910B / 310B 系列对应 A2，910C 或 `Ascend910_93xx` 对应 A3，950 / 950PR / 950DT 对应 A5。通用的 `Ascend910` 需结合 `NPU Name` 的 93xx 型号信息，芯片名称中的数字后缀不作为产品代际。含明确 A2/A3/A5 标记的服务器型号也可补充识别依据。无法确认环境类型时显示 **“未知环境 - 地址”**。

服务器型号优先使用 `dmidecode -s system-product-name`。命令不可用或权限不足时尝试读取 `/sys/class/dmi/id/product_name`，不会调用 sudo；两者均未取得有效型号时进入“型号未识别”分区，型号查询失败不影响已取得的 NPU 指标。服务器型号、型号来源和查询失败原因可在节点“详细信息”中查看。

型号查询与指标采集共用一条持久 SSH 连接和本次采集的超时预算。已识别信息在当前连接中缓存；未识别信息每隔至少 60 秒重试，重新连接后重新查询。服务器型号在 NPU 命令返回错误时仍会尝试获取，便于归类故障节点。

命令和型号字段参考 [昇腾官方 SoC 查询说明](https://www.hiascend.com/doc_center/source/en/CANNCommunityEdition/910/devaids/aoe/aoepar_16_060.html)、[昇腾官方 A2/A3 板卡查询示例](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/900/devaids/optool/docs/zh/quick_start/installation_guide.md)，产品代际映射参考 [Ascend 官方项目的 A2/A3/A5 配置](https://github.com/Ascend/fbgemm-ascend/blob/main/FbgemmAscend.cmake)。

## 节点与认证配置

节点上的 `authorized_keys` 保存公钥，本机 SSH 客户端使用对应的**私钥**。填写 `id_ed25519`、`id_rsa` 等私钥路径，`.pub` 文件会被拒绝。工具只保存路径，并把路径传给 `ssh -i`，不读取或上传私钥内容。

用户名和端口在网页、JSON 配置、命令行追加节点以及连接测试中统一默认 `root` 和 `22`。省略或留空与显式填写默认值视为同一配置，保存及测试会复用相同的节点和 SSH 会话；使用其他账号或端口时明确填写。工具向 SSH 显式传递用户名和端口，SSH 配置中的 HostName、私钥及跳板机等设置仍可使用。`auth_type` 为 `key` 时使用密钥认证，该字段省略时也默认使用密钥；节点专用私钥优先于默认私钥，私钥路径留空时使用 SSH 配置和 `ssh-agent`。加密私钥需事先解锁并加入 `ssh-agent`。密钥登录启用 `BatchMode`，不会弹出密码提示；指定私钥时启用 `IdentitiesOnly`。

在 Windows 的 `%USERPROFILE%\.ssh\config` 或 Linux / macOS 的 `~/.ssh/config` 中，可为节点配置登录参数。例如将下列占位值替换为实际信息：

```sshconfig
Host example-npu
    HostName your-node-address
    User root
    Port 22
    IdentityFile C:/Users/yourname/.ssh/id_ed25519
    IdentitiesOnly yes
```

看板填写地址 `example-npu` 并选择密钥登录后，用户名和端口留空时使用 `root`、`22`，私钥留空时沿用匹配的 SSH 配置。配置以传给 SSH 的地址或别名匹配 `Host`，填写别名时需使用对应的别名。Linux / macOS 将示例私钥路径换为该系统中的实际路径。迁移环境时需要在新环境配置 SSH 私钥或填写实际私钥路径，仓库不携带个人 SSH 配置或密钥。

`auth_type` 为 `password` 时，将 SSH 登录密码填写在节点的 `password` 字段或看板的“节点密码”输入框。该模式只使用密码认证，私钥字段不参与连接，服务器需允许 SSH 密码登录。每次建立连接尝试一次密码，连接成功后继续复用；密码错误时显示实际认证失败信息。

密码**以明文保存在本机配置文件**，输入框显示为密码掩码。网页接口和 `--once` 输出不返回密码，只返回 `password_set` 标记；重新编辑时输入框为空，留空保留已存密码，填写新密码则替换，切换密钥登录会清除已存密码。密码不会放入 SSH 命令行或环境变量，辅助程序通过一次性本机回环接口读取它，两种认证方式均校验服务器指纹。辅助接口不使用系统代理，也不访问公网。

更新工具后，停止启动窗口中的旧服务并重新运行 `start.cmd` 或 `python src/python/monitor.py`，即可加载当前功能。Linux / macOS 使用随工具提供的 Python 与 shell 辅助脚本；Windows 首次使用密码登录时，通过系统自带的 .NET Framework 编译器从源码生成辅助程序。

示例：

```json
{
  "interval": 5,
  "timeout": 15,
  "identity_file": "C:/Users/yourname/.ssh/id_ed25519",
  "nodes": [
    {
      "host": "example-key-node",
      "user": "your-user",
      "port": 22,
      "auth_type": "key"
    },
    {
      "host": "example-password-node",
      "auth_type": "password",
      "password": "replace-with-node-password"
    }
  ]
}
```

支持 IP、主机名和 `~/.ssh/config` 的 Host 别名，包括配置中的跳板机。相对私钥路径以配置文件所在目录为基准。最多监控 64 个节点。

首次连接的新节点需要先使用普通 `ssh` 核对服务器指纹并录入本机 `known_hosts`；工具启用 `StrictHostKeyChecking=yes`。

## 命令行

```bash
# 使用指定配置、默认私钥、2 秒采集周期
python src/python/monitor.py --config nodes.json --identity ~/.ssh/id_ed25519 --interval 2

# 追加节点，可重复 --node；省略用户名时使用 root，节点端口默认 22
python src/python/monitor.py --node your-user@example-node --node example-alias

# 设置采集总超时
python src/python/monitor.py --timeout 20

# 完全离线演示：不建立 SSH 连接，也不覆盖正式配置
python src/python/monitor.py --demo

# 单次采集，输出 JSON，不启动网页
python src/python/monitor.py --once

# 服务启动时不自动打开浏览器
python src/python/monitor.py --no-browser --port 8766
```

命令行覆盖项作用于本次运行；在看板点击“应用”或“保存配置”时会将当前配置保存到文件。演示模式修改只保留在本次运行的内存中。

`--once` 返回码：**0** 全部节点输出已识别，**1** 至少一个节点采集失败或格式未识别，**2** 配置或启动错误。设备的 Health 属于独立字段，采集成功且 Health 为 Critical 时会显示健康异常，单次采集仍返回 0。

## 连接和故障

每个节点复用一条持久 SSH 连接。调整周期、暂停和继续不会重新登录。更换连接参数、认证方式或密码，以及移除节点时，关闭对应连接；连接断开或超时会在下一周期自动重连。单次超时覆盖 SSH 连接和命令执行全过程。

错误节点显示实际错误、命令返回码和本次原始输出，其余节点继续采集。失败节点的本次指标清空，最近成功时间保留；不会将上一份成功指标显示为本次数据。

- `Permission denied (publickey)`：检查用户名、私钥和节点的公钥配置。
- `Permission denied (password)`：检查用户名、节点密码，以及服务器是否允许该账号使用 SSH 密码登录。
- `Host key verification failed`：先通过普通 SSH 核对服务器指纹。
- 返回码 `127`：宿主机上未找到 `npu-smi` 命令，请检查昇腾驱动安装及 SSH 环境的 PATH。
- 超时：检查节点连通性或增大“采集超时”。
- 输出未识别：原始输出仍可查看；解析器支持常见双行 NPU 表格和带可选容器 PID 的进程表。

工具通过 SSH 在节点宿主机直接执行固定的 `npu-smi info` 命令及型号查询命令，自动补充常见驱动工具目录到 PATH。节点配置只需填写连接和认证信息。

## 验证

```bash
python -m unittest discover -s tests -v
```

前端排序与目录自动保存可使用 Node.js 单独验证（运行看板不需要 Node.js）：

```bash
node tests/test_node_sort.js
node tests/test_directory_sort.js
```

测试使用完全合成的 `npu-smi` 双行表格，覆盖设备、健康状态、进程与容器 PID、无 HBM 表格、未知指标、密钥路径和配置校验、地址与 SSH 别名去重、自动命名、A2/A3/A5 型号识别、服务器型号与只读回退、型号查询缓存及故障隔离、两种认证方式下的 SSH 会话复用、密码错误、密码保留与替换、切换密钥时清除密码、网页接口不返回密码、未保存节点的连接测试、测试与采集并发复用、SSH 连接与 NPU 命令故障区分、测试不修改配置与历史、断线重连、超时恢复、输出上限、暂停和动态周期、节点故障隔离及本地接口访问约束。原生 OpenSSH 密码辅助测试使用临时加密测试密钥，传输测试使用本地模拟子进程，HTTP 测试只连接本机；测试凭据均为合成数据，不会登录真实节点。

表格字段参考 [昇腾官方 npu-smi 输出示例](https://www.hiascend.com/document/detail/zh/mindcluster/70rc1/faultdiag/faultdiagug/mindxdlFDUG027.html)。

密码辅助程序使用 [OpenSSH 的 SSH_ASKPASS 机制](https://man.openbsd.org/ssh#ENVIRONMENT)。

## 许可证

本项目使用 [MIT License](LICENSE)。
