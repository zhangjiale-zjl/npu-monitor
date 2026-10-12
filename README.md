# 本地 NPU 节点监控

通过 SSH 密钥或密码认证定时执行 `npu-smi info`，在本机浏览器查看多个节点的 NPU 状态。运行依赖 **Python 3.10+、系统 OpenSSH 客户端和浏览器**，无需 pip、npm 或公网服务。Windows 密码登录的本地辅助程序从仓库中的源码自动生成，使用系统自带的 .NET Framework 编译器。

看板仅监听 `127.0.0.1`，网页资源全部随工具提供。采集通过 SSH 连接指定节点，因此本机需能访问这些节点的 SSH 端口。支持 Windows、Linux 和 macOS；远端需提供 POSIX `sh` 和 `npu-smi`。

## 启动

首次启动显示空看板，可点击“＋ 添加节点”填写自己的连接信息。也可将 `nodes.example.json` 复制为 `nodes.json` 后修改；示例中的地址、用户名和密码均为占位内容。`nodes.json`、采集快照、截图和本机生成的辅助程序不纳入版本控制。

Windows 双击 **`start.cmd`**，自动打开本地看板。启动脚本优先查找已安装的 Python，也支持当前 Codex 自带的 Python。保持启动窗口运行，按 **Ctrl+C** 停止采集并关闭 SSH 连接。

也可在终端启动：

```powershell
python monitor.py
```

Linux / macOS：

```bash
python3 monitor.py --config nodes.json
```

浏览器地址：`http://127.0.0.1:8765`。端口被占用时使用 `--port 8766` 或 `--port 0` 自动分配。

## 使用看板

- **添加节点**：点击顶部“＋ 添加节点”，填写地址 / SSH 别名、用户名和端口，选择“密钥登录”或“密码登录”，填写私钥路径或节点密码，点击“保存配置”即可开始监控。名称自动生成“环境类型 - IP”；用户名、端口留空时沿用本机 SSH 配置。相同连接地址与端口只保留首次配置，保存时提示合并数量。
- **管理 / 编辑节点**：顶部“管理节点”可批量增删、修改节点及默认配置；每个节点的“编辑”按钮定位到该节点。保存后立即生效，并写入启动时指定的配置文件，重启后自动加载。采集暂停时，保存配置会保持暂停状态。
- **监控周期**：输入 1–3600 秒并点击“应用”。周期按每个节点的采集开始时间计算，单节点采集互不重叠；采集耗时超过周期时，完成后尽快开始下一次。
- **立即采集 / 暂停采集 / 继续采集**：控制全部节点。暂停时正在进行的采集可以完成，页面显示最近一次结果。
- **全部节点总览**：页面默认按服务器型号分区，每个分区直接展示所有节点，每个节点使用紧凑表格，无需选择节点。分区标题显示节点数、芯片数和采集异常数；型号未识别的节点集中显示在页面末尾。布局根据窗口宽度自动调整列数，1280 像素宽的窗口可并排显示 3 个节点；节点较多时在同一页面滚动查看。
- **每张 NPU 卡**：每行对应一个 NPU / 芯片，主表直接显示 **Health 原始状态、AICore（%）、HBM（%）、Memory（%）**，Health 非 OK 的卡标红。悬停内存占用率可查看已用 / 总量（GiB、MB）；展开“详细信息 → 内存明细”可逐卡查看容量。比例未提供且容量已知时显示原始 MB 数值，例如 `0/0`，数据缺失时显示 `—`。悬停 NPU 可查看型号、Bus、温度及功耗。
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

用户名和端口留空时，OpenSSH 使用本机现有配置。`auth_type` 为 `key` 时使用密钥认证，该字段省略时也默认使用密钥；节点专用私钥优先于默认私钥，私钥路径留空时使用 SSH 配置和 `ssh-agent`。加密私钥需事先解锁并加入 `ssh-agent`。密钥登录启用 `BatchMode`，不会弹出密码提示；指定私钥时启用 `IdentitiesOnly`。

`auth_type` 为 `password` 时，将 SSH 登录密码填写在节点的 `password` 字段或看板的“节点密码”输入框。该模式只使用密码认证，私钥字段不参与连接，服务器需允许 SSH 密码登录。每次建立连接尝试一次密码，连接成功后继续复用；密码错误时显示实际认证失败信息。

密码**以明文保存在本机配置文件**，输入框显示为密码掩码。网页接口和 `--once` 输出不返回密码，只返回 `password_set` 标记；重新编辑时输入框为空，留空保留已存密码，填写新密码则替换，切换密钥登录会清除已存密码。密码不会放入 SSH 命令行或环境变量，辅助程序通过一次性本机回环接口读取它，两种认证方式均校验服务器指纹。辅助接口不使用系统代理，也不访问公网。

更新工具后，停止启动窗口中的旧服务并重新运行 `start.cmd` 或 `python monitor.py`，即可加载当前功能。Linux / macOS 使用随工具提供的 Python 与 shell 辅助脚本；Windows 首次使用密码登录时，通过系统自带的 .NET Framework 编译器从源码生成辅助程序。

示例：

```json
{
  "interval": 5,
  "timeout": 15,
  "identity_file": "C:/Users/yourname/.ssh/id_ed25519",
  "npu_smi_path": "npu-smi",
  "nodes": [
    { "host": "example-key-node", "user": "your-user", "port": 22, "auth_type": "key" },
    {
      "host": "example-password-node",
      "auth_type": "password",
      "password": "replace-with-node-password",
      "npu_smi_path": "/usr/local/bin/npu-smi"
    }
  ]
}
```

支持 IP、主机名和 `~/.ssh/config` 的 Host 别名，包括配置中的跳板机。相对私钥路径以配置文件所在目录为基准。最多监控 64 个节点。

首次连接的新节点需要先使用普通 `ssh` 核对服务器指纹并录入本机 `known_hosts`；工具启用 `StrictHostKeyChecking=yes`。

## 命令行

```bash
# 使用指定配置、默认私钥、2 秒采集周期
python monitor.py --config nodes.json --identity ~/.ssh/id_ed25519 --interval 2

# 追加节点，可重复 --node；用户名留空时使用 SSH 配置
python monitor.py --node your-user@example-node --node example-alias

# 指定远端 npu-smi 路径和采集总超时
python monitor.py --npu-smi /usr/local/bin/npu-smi --timeout 20

# 完全离线演示：不建立 SSH 连接，也不覆盖正式配置
python monitor.py --demo

# 单次采集，输出 JSON，不启动网页
python monitor.py --once

# 服务启动时不自动打开浏览器
python monitor.py --no-browser --port 8766
```

命令行覆盖项作用于本次运行；在看板点击“应用”或“保存配置”时会将当前配置保存到文件。演示模式修改只保留在本次运行的内存中。

`--once` 返回码：**0** 全部节点输出已识别，**1** 至少一个节点采集失败或格式未识别，**2** 配置或启动错误。设备的 Health 属于独立字段，采集成功且 Health 为 Critical 时会显示健康异常，单次采集仍返回 0。

## 连接和故障

每个节点复用一条持久 SSH 连接。调整周期、暂停和继续不会重新登录。更换连接参数、认证方式或密码，以及移除节点时，关闭对应连接；连接断开或超时会在下一周期自动重连。单次超时覆盖 SSH 连接和命令执行全过程。

错误节点显示实际错误、命令返回码和本次原始输出，其余节点继续采集。失败节点的本次指标清空，最近成功时间保留；不会将上一份成功指标显示为本次数据。

- `Permission denied (publickey)`：检查用户名、私钥和节点的公钥配置。
- `Permission denied (password)`：检查用户名、节点密码，以及服务器是否允许该账号使用 SSH 密码登录。
- `Host key verification failed`：先通过普通 SSH 核对服务器指纹。
- 返回码 `127`：节点上找不到 `npu-smi`，在配置中填写其绝对路径。
- 超时：检查节点连通性或增大“采集超时”。
- 输出未识别：原始输出仍可查看；解析器支持常见双行 NPU 表格和带可选容器 PID 的进程表。

工具执行节点宿主环境中的命令。远端命令路径须为 `npu-smi` 或可执行文件的绝对路径。

## 验证

```bash
python -m unittest discover -s tests -v
```

测试使用完全合成的 `npu-smi` 双行表格，覆盖设备、健康状态、进程与容器 PID、无 HBM 表格、未知指标、密钥路径和配置校验、地址与 SSH 别名去重、自动命名、A2/A3/A5 型号识别、服务器型号与只读回退、型号查询缓存及故障隔离、两种认证方式下的 SSH 会话复用、密码错误、密码保留与替换、切换密钥时清除密码、网页接口不返回密码、断线重连、超时恢复、输出上限、暂停和动态周期、节点故障隔离及本地接口访问约束。原生 OpenSSH 密码辅助测试使用临时加密测试密钥，传输测试使用本地模拟子进程，HTTP 测试只连接本机；测试凭据均为合成数据，不会登录真实节点。

表格字段参考 [昇腾官方 npu-smi 输出示例](https://www.hiascend.com/document/detail/zh/mindcluster/70rc1/faultdiag/faultdiagug/mindxdlFDUG027.html)。

密码辅助程序使用 [OpenSSH 的 SSH_ASKPASS 机制](https://man.openbsd.org/ssh#ENVIRONMENT)。

## 许可证

本项目使用 [MIT License](LICENSE)。
