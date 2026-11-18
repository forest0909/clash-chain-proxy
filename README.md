# Clash Chain Proxy (`clash-chain-proxy`)

为 **Clash Verge Rev** 选定订阅配置 SOCKS5 链式代理的 Agent Skill 与命令行配置工具。默认附带 Claude 分流规则，支持客户自行填写或配置 SOCKS5 连接信息、追加自定义分流规则、只读变更预览、扫描已配置订阅、自动备份与安全还原。

## 核心特性

- **标准链式代理拓扑**：自动为目标订阅注入 SOCKS5 节点 `MyAI`（设置 `dialer-proxy` 指向订阅首个代理组）以及 `select` 选择组 `MyIP`（可选 `MyAI` 或上游代理组），链路为：
  ```text
  设备 → 订阅上游代理组 → SOCKS5 节点 (MyAI) → 目标服务
  ```
- **内置分流规则**：默认在 `assets/claude-rules.yaml` 中附带 27 条 Claude / Anthropic 域名、IP-CIDR、进程名及出口检测页（`ip.net.coffee`）分流规则，保留订阅原有规则。
- **支持追加自定义规则**：可通过用户配置文件追加 `DOMAIN`、`DOMAIN-SUFFIX`、`DOMAIN-KEYWORD`、`IP-CIDR`、`IP-CIDR6`、`PROCESS-NAME` 规则（出口固定为 `MyIP`）。
- **防循环脚本包装**：保留原订阅扩展脚本 `main()` 逻辑，并在其后追加防循环包装器，从非 `MyIP` 代理组的显式 `proxies` 列表中过滤 `MyAI` 与 `MyIP`。
- **安全与可逆设计**：
  - 默认只读预览，明确传入 `--apply` 才会写入文件。
  - 每次实际修改前自动在 `<配置目录>/.claude-chain-backups/<备份编号>/` 创建备份及 `manifest.json`（含 SHA-256 校验），还原时若检测到后续人工修改会主动中止，防止覆盖。
  - 接管保护：遇到来源不明的同名 `MyAI` / `MyIP` 对象时自动停止，需确认并显式传入 `--adopt-existing` 才接管。
  - 扫描与预览过程不泄露服务器地址、端口、用户名、密码或订阅链接等敏感信息。

---

## 目录结构

```text
clash-chain-proxy/                # 仓库根目录（可直接作为 Skill 目录安装）
├── SKILL.md                      # Agent Skill 主指令与工作流定义
├── README.md                     # 项目说明文档
├── requirements.txt              # Python 依赖（PyYAML）
├── .gitignore                    # Git 忽略规则
├── agents/
│   └── openai.yaml               # Skill UI 元数据配置
├── assets/
│   ├── claude-rules.yaml         # 内置 Claude 分流规则集
│   └── user-config.example.yaml  # 用户配置文件模板
├── scripts/
│   └── configure.py              # 核心配置、扫描、备份与还原 CLI 脚本
├── tools/
│   └── package.py                # 打包脚本（生成 dist/clash-chain-proxy.skill）
├── dist/
│   └── clash-chain-proxy.skill   # 可直接导入的 .skill 分发归档包
└── tests/
    ├── test_configure.py         # 配置逻辑与安全扫描单元测试
    └── test_package.py           # Skill 打包产物单元测试
```

---

## 安装与使用

### 环境要求

- Python 3
- `PyYAML`（可通过 `pip install -r requirements.txt` 安装）
- Clash Verge Rev（目标订阅需已在 Clash Verge Rev 中打开并保存过**节点、代理组、规则、脚本**四个扩展关联，脚本本身不直接改写 `profiles.yaml`）

### 方式一：作为 Agent Skill 使用

1. **直接导入打包文件**：使用 [`dist/clash-chain-proxy.skill`](dist/clash-chain-proxy.skill) 安装到支持 `.skill` 的 AI Agent 客户端。
2. **源码目录安装**：将本仓库直接克隆或软链接到你的 Skills 目录（例如 `~/.agents/skills/clash-chain-proxy` 或对应客户端的 skills 目录），Agent 会自动读取根目录下的 [`SKILL.md`](SKILL.md)。

### 方式二：作为独立 CLI 工具运行

脚本默认自动探测常见操作系统下的 Clash Verge Rev 配置目录（`io.github.clash-verge-rev.clash-verge-rev`）：
- macOS：`~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev`
- Windows：`%APPDATA%\io.github.clash-verge-rev.clash-verge-rev`
- Linux：`$XDG_DATA_HOME/io.github.clash-verge-rev.clash-verge-rev` 或 `~/.local/share/io.github.clash-verge-rev.clash-verge-rev`

若未自动检测到唯一目录，可通过 `--dir "配置目录"` 手动指定包含 `profiles.yaml` 的目录。

#### 1. 查看订阅列表

```bash
# 列出所有订阅（uid、name、current）
python3 scripts/configure.py --list

# 只读扫描已配置 MyAI/MyIP 链式代理的订阅
python3 scripts/configure.py --list-configured
```

#### 2. 生成待填配置（推荐在 Clash 客户端手填连接信息）

```bash
# 仅预览
python3 scripts/configure.py --subscription "<UID或订阅名>" --prepare

# 确认写入并自动备份
python3 scripts/configure.py --subscription "<UID或订阅名>" --prepare --apply
```

写入后，在 Clash Verge Rev 中：
1. 进入左侧 **订阅**，右键目标订阅选择 **编辑节点** → 右上角 **高级**。
2. 在 `prepend` 中找到名为 `MyAI` 的节点，填入 `server`、`port`、`username`、`password`（无认证可留空后两项）并保存。
3. 重新点击激活该订阅，在代理页确认 `MyIP → MyAI`，并在上游代理组选择可用节点。

#### 3. 通过外部配置文件写入连接信息或追加规则

复制模板文件到**仓库外的个人目录**（请勿将真实凭证提交到仓库）：

```bash
cp assets/user-config.example.yaml ~/clash-chain-user-config.yaml
```

配置文件结构如下：

```yaml
connection:
  server: '203.0.113.10'
  port: 1080
  username: 'your-username'
  password: 'your-password'
custom_rules:
  - DOMAIN-SUFFIX,example.com,MyIP
  - DOMAIN,api.example.org,MyIP
  - PROCESS-NAME,ExampleApp,MyIP
  - IP-CIDR,192.0.2.0/24,MyIP,no-resolve
```

> 说明：`connection` 中的空字段表示**沿用现存 `MyAI` 节点中已有的值**，不会把客户手动填写的信息覆盖为空。

执行预览与写入：

```bash
# 仅预览变更（不修改文件）
python3 scripts/configure.py --subscription "<UID或订阅名>" --config ~/clash-chain-user-config.yaml

# 确认写入
python3 scripts/configure.py --subscription "<UID或订阅名>" --config ~/clash-chain-user-config.yaml --apply
```

#### 4. 备份与还原

每次使用 `--apply` 产生实质文件变更时，都会输出一个备份编号。还原前可先预览，再加 `--apply` 执行：

```bash
# 预览还原影响
python3 scripts/configure.py --restore "<备份编号>"

# 确认还原
python3 scripts/configure.py --restore "<备份编号>" --apply
```

#### 5. 出口验证

激活订阅后，在本机浏览器打开 `https://ip.net.coffee/claude/`，并在 Clash Verge Rev 连接记录中确认请求实际经过 `MyIP → MyAI`。

---

## 开发、测试与打包

### 运行单元测试

```bash
python3 -m unittest discover -s tests -v
```

### 重新打包 `.skill` 产物

修改 Skill 相关文件（`SKILL.md`、`agents/`、`assets/`、`scripts/`）后，运行打包脚本重新生成 `dist/clash-chain-proxy.skill`：

```bash
python3 tools/package.py
```

打包脚本仅收录运行时所需的 Skill 核心文件，会自动排除 `README.md`、`tests/`、`tools/`、`dist/` 以及 `__pycache__` 等缓存文件。
