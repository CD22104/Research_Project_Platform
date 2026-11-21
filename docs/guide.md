# 使用说明

这一页是要你自己动手的操作。带 `!` 的命令，在 a100 上任意一个 Claude 会话的输入框里输入（例如「A100: 研究服务台」），`!` 会让它以你的身份直接运行；也可以去掉 `!`，在 a100 的终端里运行。

## 项目的常驻入口

每个项目在 a100 上有一个常驻入口：后台 tmux 里运行着的 Claude（服务模式），你在手机 Claude App 或 claude.ai/code 里点项目名就能开新会话，不用碰终端。本页上方的表格显示每个项目的入口现在是否在运行。

### 新建项目后，起它的入口

```
! ~/bin/rc-start <项目名>
```

第一次在这个项目目录启动时，Claude 会问是否信任这个目录。由你回答：

```
! tmux send-keys -t '=rc-<项目名>:' y Enter
```

每个目录只问一次，以后重启不会再问。大约一分钟后，在 App 或 claude.ai/code 里就能看到这个项目名。

### a100 重启之后

入口不会自动恢复（看板会）。运行一次，把所有项目的入口都拉起来：

```
! ~/bin/rc-start --all
```

### 其他操作

| 想做什么 | 命令 |
|---|---|
| 看哪些入口在运行 | `! ~/bin/rc-start --status` |
| 重启某个项目的入口（约一分钟后恢复） | `! tmux kill-session -t =rc-<项目名>; ~/bin/rc-start <项目名>` |
| 关掉某个项目的入口 | `! tmux kill-session -t =rc-<项目名>` |
| 看入口的启动记录 | `! tail ~/.local/state/rc/<项目名>.log` |

### 要记住的

- 断网约 10 分钟入口会退出，脚本会在一分钟后自动重启，并尽量接回原来的会话。
- 入口只等你说话，不会自己开始干活。
- 同一个项目不要同时在两个会话里改文件，会互相覆盖。

## 打开这个控制台

- Mac 上直接打开 http://localhost:8765/ 。隧道由 Mac 上的 LaunchAgent 维持。
- 隧道不通时，手动开一个：`ssh -N -L 8765:/home/dchen/gpu-board/run/board.sock dchen@xtraa100.ddns.comp.nus.edu.sg`
- 控制台只有你能访问；a100 重启后会自动启动。

## 密钥和 token

放在各台服务器的 `~/.config/secrets/`，一个服务一个文件，只有你能读写。

| 机器 | 有哪些 |
|---|---|
| 6k04 | 全部 10 个（含 OpenReview 账号密码）；原件在 `~/.config/agent-safety/keys.env` |
| a100 | 除 OpenReview 外的 9 个 |
| 6k01、6k03、h100、h200 | 只有 `huggingface.env` |

- 更换某个 token 后，要同时更新：6k04 的原件和拆分出的文件、a100 的对应文件，HuggingFace 的还要更新另外四台。跟 a100 的会话说一声，它会按同样方式重新分发，不会显示 token 的值。
- 不要把 token 放进 `.bashrc`、共享盘或项目目录；不要在环境里设置 `ANTHROPIC_API_KEY`（会让远程控制失效）。

## 进度记录的格式

各项目 `board/` 目录怎么写，见 `/shared/ssd/dchen/_shared/BOARD_FORMAT.md`。项目页底部的「记录检查」会列出写错的地方。
