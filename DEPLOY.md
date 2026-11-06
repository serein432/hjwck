# 智能语音播报系统 — 部署说明

## 一、系统组成

| 组成 | 说明 |
|---|---|
| 后端服务 | Python + FastAPI，端口 9090，SQLite 持久化（`data/vsb.db`） |
| 桌面 App | pywebview 壳（Windows WebView2，系统自带），加载 `app/desktop/index.html` |
| 语音栈 | 唤醒(能量/Porcupine) + ASR(Vosk离线/在线占位) + NLU(规则意图) + 技能路由 + TTS(edge-tts在线/SAPI离线兜底) |
| 触发源 | 定时(cron) / HTTP / MQTT / 数据库轮询 / 传感器(模拟) / 按钮 |

## 二、环境要求

- Windows 10/11（WebView2 Runtime，Win11 自带）
- Python 3.10+（当前 3.10.11）
- 麦克风（语音交互页实时录音用；无麦自动降级为模拟信号，闭环仍可演示）
- 扬声器（播报播放用；SAPI 离线兜底无需联网）

## 三、安装依赖

```powershell
cd d:\python文件夹\voice-broadcast-system
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

核心依赖：`fastapi` `uvicorn` `pywebview` `sounddevice` `soundfile` `edge-tts` `paho-mqtt` `apscheduler` `vosk`(离线ASR) `pywin32`(SAPI兜底)。

## 四、启动（桌面 App，推荐）

**方式 A（推荐，双击启动）**：直接双击 `启动App.bat`

**方式 B（命令行）**：

```powershell
python app/desktop/app_desktop.py
```

- 单进程启动：内嵌线程跑后端 + 打开桌面窗口
- 关闭窗口即自动终止后端
- 首次启动需等待后端就绪（约 2~5 秒）
- `启动App.bat` 会自动检查 Python 与依赖，缺失时自动安装（清华镜像）

## 五、启动（仅后端服务，用于外部系统接入）

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 9090
```

- 接口文档：`http://127.0.0.1:9090/docs`
- 健康检查：`GET http://127.0.0.1:9090/health`

## 六、配置项

- 唤醒词、音色、语速、音量、静音时段、在线/离线模式、回声策略：在 App「设置中心」界面修改（热更新生效，需管理员）
- 默认账号：`admin / admin123`（管理员）、`user / user123`（普通用户）
- 系统 API Token：`vsb-dev-token`（见 `app/config/settings.py` → `server.api_token`，**生产环境务必修改**）
- 对外 HTTP 触发：`POST /api/v1/broadcast`，请求头 `Authorization: Bearer <token>`

## 七、数据与日志

- 数据库：`data/vsb.db`（SQLite，含播报日志/模板/触发源/技能/对话记录）
- 日志：`data/logs/vsb.log`（运行日志）、`vsb-audit.log`（审计）
- 音频缓存：`data/audio/`

## 八、离线/在线切换

| 能力 | 在线 | 离线 |
|---|---|---|
| TTS 合成 | edge-tts（音色丰富） | Windows SAPI（系统语音，质量一般但稳定） |
| ASR 识别 | 在线占位（未启用） | Vosk 中文小模型（需下载模型） |
| 唤醒 | 能量+关键词 | 能量+关键词 |

- 默认「混合模式」：TTS 优先 edge-tts，失败/无网自动降级 SAPI
- 纯离线：设置中心「在线模式」选「纯离线」
- Vosk 中文模型：`python scripts/download_models.py`（首次离线 ASR 前需下载）

## 九、打包为 exe（可选）

当前以 `python app_desktop.py` 方式运行，依赖本机 Python 环境。若需单文件 exe 分发：

```powershell
pip install pyinstaller
pyinstaller --onefile --name VoiceBroadcast --add-data "app/desktop/index.html;app/desktop" app/desktop/app_desktop.py
```

> 注意：`--onefile` 模式 pywebview 需 WebView2；打包前建议在干净机器验证。ASR 模型体积较大，建议放数据目录而非打进 exe。
