# 抖音指定收藏夹批量下载器

批量下载当前抖音账号中**某一个指定收藏夹的全部作品**。

## 需求行为

- 以抖音 **作品 ID** 持久去重，重复运行只下载新增或缺失的作品。
- 普通视频保存到 `下载/<收藏夹名称_ID>/视频/`。
- 普通图集保存原始图片；实况/动态图集仍按图集处理，并将图片自带的动态视频保存为 `001_动图.mp4`。
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


## 作者目录创建规则

同一收藏夹中，同一作者累计达到 **3 个不同作品** 时，才会创建 `作者/<作者昵称>/` 目录并归类该作者的作品；只有 1–2 个作品时不创建。图集无论包含多少张图片或动图，都只按 1 个作品计算。

## 登录状态

登录数据保存在本目录的 `browser_data/`，已由 `.gitignore` 排除，不会上传。首次运行时扫码登录即可。

如果要复用其他安装目录中的登录状态，可以在启动程序前设置 `DOUYIN_SOURCE_PROFILE`，例如：

```bat
set "DOUYIN_SOURCE_PROFILE=D:\旧目录\browser_data"
下载指定收藏夹.cmd
```

若登录失效：关闭程序和它打开的 Chrome，删除本目录 `browser_data/`，重新运行并扫码。

> 仅下载你有权保存的内容，并遵守平台规则与版权法律。
