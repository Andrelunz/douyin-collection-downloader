# 抖音指定收藏夹批量下载器

批量下载当前抖音账号中**某一个指定收藏夹的全部作品**。

## 需求行为

- 以抖音 **作品 ID** 持久去重，重复运行只下载新增或缺失的作品。
- 普通视频保存到 `下载/<收藏夹名称_ID>/视频/`。
- 普通图集保存原始图片；实况/动态图集仍按图集处理，并将图片自带的动态视频保存为 `001_动图.mp4`。
- 不下载抖音为图集生成的整段轮播视频，也不下载配乐。
- 每个图集作品单独一个文件夹：
  `下载/<收藏夹名称_ID>/图集/<作品标题_作品ID>/001.jpg ...`
- 下载通过 `.part` 临时文件原子落盘；中断后重跑不会把残缺文件当成成功。
- 失败作品不会写入完成记录，重新运行即可重试。

## 安装与使用

仅支持 Windows 10/11。首次使用：

1. 安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)；也可以在 PowerShell 中执行 `winget install --id=astral-sh.uv -e`。
2. 下载并解压本项目，双击 **`安装.cmd`** 创建 Python 3.11 环境、安装依赖并运行测试。
3. 双击 **`下载指定收藏夹.cmd`**。
4. 浏览器首次出现时扫码登录抖音；以后会复用登录状态。
5. 程序列出收藏夹后，输入序号或完整名称。
6. 等待完成；以后选择同一收藏夹时，会自动跳过已完成作品。

也可以双击：

| 文件 | 用途 |
|---|---|
| `查看收藏夹.cmd` | 只列出收藏夹，不下载 |
| `按名称下载收藏夹.cmd` | 先输入收藏夹名称再下载 |
| `安装.cmd` | 首次安装，或环境损坏后重装依赖 |

## 命令行

```bat
.venv\Scripts\python.exe douyin_collection_downloader.py --list
.venv\Scripts\python.exe douyin_collection_downloader.py --collection "风景"
.venv\Scripts\python.exe douyin_collection_downloader.py --collection "风景" --dry-run
```

常用选项：

- `--workers 3`：并发下载数，允许 1–6。
- `--retries 3`：每个文件的重试次数。
- `--page-delay 1.0`：读取收藏夹分页的间隔秒数。

## 去重与断点续传

完成记录位于 `data/download_state.sqlite3`，键为“收藏夹 ID + 作品 ID”。程序还会核对记录中所有文件是否仍存在且不是空文件：

- 记录存在、文件完整：跳过。
- 记录存在、文件被删/损坏：重新下载该作品。
- 图集中任意一张失败：该作品不记为完成，下次重跑会补齐缺失图片，已有图片不会重复下载。
- 同名作品也不会覆盖，因为文件夹/文件名包含作品 ID。

不要删除 `data/download_state.sqlite3`；删除后文件存在检查仍可避免覆盖同名文件，但无法可靠识别已改标题或已移动的作品。

## 登录状态

登录数据保存在本目录的 `browser_data/`，已由 `.gitignore` 排除，不会上传。首次运行时扫码登录即可。

如果要复用其他安装目录中的登录状态，可以在启动程序前设置 `DOUYIN_SOURCE_PROFILE`，例如：

```bat
set "DOUYIN_SOURCE_PROFILE=D:\旧目录\browser_data"
下载指定收藏夹.cmd
```

若登录失效：关闭程序和它打开的 Chrome，删除本目录 `browser_data/`，重新运行并扫码。

## 项目选择说明

调研的主要候选：

1. [JoeanAmier/TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader)（DouK-Downloader）：约 1.5 万 Star，功能最完整，明确支持抖音收藏夹、图集、下载 ID 记录和作品独立目录；因此作为设计参考最有价值。但其当前 README 明确提示抖音加密参数算法已经失效、扫码登录失效，且源码要求 Python 3.12，直接部署其当前版不能保证收藏夹可用。
2. [laigus/MediaCollector](https://github.com/laigus/MediaCollector)：有友好的 Web UI 和收藏夹选择，但项目很新；检查源码发现下载记录是任务级的，删除/新建任务会失去跨任务去重，而且图集图片平铺在收藏夹目录，不满足“每个图集单独文件夹”。
3. [renyijiu/douyin_downloader](https://github.com/renyijiu/douyin_downloader)：已归档，最后代码更新较早，不适合当前抖音接口。

最终实现采用 DouK-Downloader 的成熟设计思想（作品 ID 去重、图集图片、作品目录），结合本机已验证可工作的 DrissionPage 浏览器同源 API：不依赖已失效的签名算法，能读取登录账号的真实收藏夹。

> 仅下载你有权保存的内容，并遵守平台规则与版权法律。
