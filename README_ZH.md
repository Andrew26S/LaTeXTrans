<div align="center">

[English](README.md) | 中文



<img src="./logo.png" width="1000px"></img>

  **Turn arXiv Papers into Multilingual Masterpieces**
#
<!-- <p align="center">
  <a href="https://arxiv.org/abs/2503.06594" alt="paper"><img src="https://img.shields.io/badge/Paper-LaTeXTrans-blue?logo=arxiv&logoColor=white"/></a>
</p> -->

</div>

<div align="center">
<p dir="auto">

• 📖 [介绍](#-介绍) 
• 🛠️ [安装指南](#️-安装指南) 
• ⚙️ [配置说明](#️-配置说明)
• 📚 [使用方式](#-使用方式)
• 🖼️ [翻译样例](#️-翻译样例) 

</p>
</div>

 从 arXiv 论文 ID 到译文 PDF 的端到端翻译。LaTeXTrans 有如下的特点和优势 :
 - **🌟 保持公式、排版和交叉引用的完整性**
 - **🌟 保证术语翻译的一致性**
 - **🌟 支持从原文 LaTeX 源码（通过提供的 arXiv 论文 id 自动下载）到译文 PDF 的端到端翻译**

借助 LaTeXTrans，研究人员和学生可以得到更高质量的论文翻译而无需担心格式混乱或内容缺失，从而更高效地阅读和理解 arXiv 论文。

# 📖 介绍

LaTeXTrans 是一个基于多智能体协作的结构化 LaTeX 文档翻译系统. 该系统能够直接翻译 LaTeX 代码，并生成与原文排版高度一致的译文 PDF。 不同于传统文档翻译方法（例如 PDF 翻译）容易破坏公式和格式，该系统使用大模型直接翻译预处理过的论文 LaTeX 源码，并通过由 Parser, Translator, Validator, Summarizer, Terminology Extractor, Generator 这六个智能体组成的工作流实现了排版一致和格式保持. 

 
# 🛠️ 安装指南

LaTeXTrans 需要 Python 3.12 或更高版本。

#### 1. 克隆仓库

```bash
git clone https://github.com/NiuTrans/LaTeXTrans.git
cd LaTeXTrans
pip install -e .
```

#### （可选）使用 Conda 虚拟环境

```bash
conda create -n latextrans python=3.12 -y
conda activate latextrans
git clone https://github.com/NiuTrans/LaTeXTrans.git
cd LaTeXTrans
pip install -e .
```

#### 2. 安装用于生成 PDF 的 TeX 环境

在 Ubuntu（含 WSL 中的 Ubuntu）上，建议用 apt 安装 TeX Live。翻译英文论文并生成中文 PDF，可以先安装编译工具、中文支持和字体。

```bash
sudo apt update
sudo apt install latexmk texlive-xetex texlive-latex-recommended texlive-lang-chinese
sudo apt install texlive-fonts-recommended fonts-noto-cjk fonts-noto-cjk-extra
```

译文用 XeLaTeX 编译。模板直接指定旧西文字体族 `ptm`、`phv`、`pcr`，或通过 `times` 等宏包选用它们时，译文会映射到对应的 TeX Gyre 字体，让正文和表格中的粗体正常显示。论文显式指定的西文字体优先。

下面几组包按论文模板的需要选装。磁盘空间有限时，可以等编译提示缺少宏包后再安装对应的包。

```bash
# 扩展宏包、科学排版和绘图
sudo apt install texlive-latex-extra texlive-science texlive-pictures

# ACM、IEEE 等出版模板及 BibTeX 参考文献样式
sudo apt install texlive-publishers texlive-bibtex-extra

# 额外字体（如 inconsolata、newtxmath）和人文排版宏包（如 dramatist）
sudo apt install texlive-fonts-extra texlive-humanities
```

最后一组可以分开安装。在 Ubuntu 24.04 上，仅 `texlive-fonts-extra` 的安装体积就超过 1 GiB。

 > [!IMPORTANT]
> Windows 用户如果选用 MiKTeX，请开启按需安装宏包，并安装 Perl 供 `latexmk` 使用。[MiKTeX](https://miktex.org/download) · [Strawberry Perl](https://strawberryperl.com/)


# ⚙️ 配置说明


首次使用时，在仓库根目录复制配置模板。CLI 和 GUI 默认都读取 `config/local.toml`。

```bash
cp -n config/default.toml config/local.toml
```

在 `config/local.toml` 的 `[llm_config]` 中填写模型名、API 密钥和完整的聊天补全接口 URL。CLI 也可以通过 `--config 路径` 指定其他配置文件。

```toml
model = " " # model name (For example, deepseek-chat)
api_key = " " # your_api_key_here
base_url = " " # base url of the API (For example, https://api.deepseek.com/v1/chat/completions)
timeout = 300 # LLM 请求超时时间,单位秒（默认100）
# max_tokens = 8192 # 可选，单次输出 token 上限，设置后才会发送（请确认模型支持的上限）
```

 > [!NOTE]
下面的例子是对于不同的模型，推荐使用的base_url：

| Model |base_url| 
|:-|:-|
|deepseek-chat|https://api.deepseek.com/v1/chat/completions|
|gpt-4o|https://api.openai.com/v1/chat/completions|
|gemini-2.5-pro|https://generativelanguage.googleapis.com/v1beta/openai/chat/completions|


# 📚 使用方式

### 通过 ArXiv ID 翻译
只需提供 arXiv 论文 ID 即可完成翻译：

```bash
latextrans --arxiv ${xxxx}
# For example, 
# latextrans --arxiv 2508.18791
```

现在也支持带版本号的 arXiv ID，可以指定论文的具体修订版本：

```bash
latextrans --arxiv 2508.18791v2
```

该命令将：

1. 从 arXiv 下载 LaTeX 源码并解压
2. 执行由解析、翻译、重构和编译组成的工作流
3. 在 outputs 文件夹保存翻译后的论文 LaTeX 项目文件和编译生成的译文PDF

每篇论文的译文保存在固定路径。例如，中文版 `2410.05160` 的目标文件是 `outputs/ch_2410.05160/ch_2410.05160.pdf`。只要这个文件存在，CLI 就会询问是否重新翻译并替换，直接回车可取消。GUI 提供 **Cancel translation** 和 **Replace translation** 两个选项。只有新译文编译成功后才会替换原文件。

### 批量翻译 ArXiv 论文

支持逗号分隔的多个 arXiv ID：

```bash
latextrans --arxiv ${xxxx}, ${xxxx}
# For example,
# latextrans --arxiv 2508.18791v2, 2407.01648
```

### 翻译本地项目

本地压缩包（`.zip/.tar/.tar.gz/.tgz`）：

```bash
latextrans --project D:\\path\\to\\paper_source.tar.gz
```

本地已解压项目目录：

```bash
latextrans --project D:\\path\\to\\paper_project_dir
```

当你提供 `--arxiv` 或 `--project` 时，LaTeXTrans 只会处理这些显式指定的输入，
不会扫描并翻译 `tex source` 下其他已有项目。

如果你确实要处理 `tex source` 下全部已有项目，请使用：

```bash
latextrans --all-existing
```

### Streamlit GUI

如果你想使用带有进度显示、日志和可配置参数的图形界面，可以运行：

```bash
latextrans-gui
```

也可以直接使用 Streamlit：

```bash
streamlit run src/gui/streamlit_app.py
```

 > [!NOTE]
尽管 LaTeXTrans 支持任意语言到任意语言的翻译，但是目前版本仅对英文到中文的翻译做了相对完善的编译适配。翻译到其他语言时，最终输出的 pdf 可能会有错误，欢迎提出 issue 来描述您遇到的问题，我们会逐个解决。

# 🖼️ 翻译样例

以下是 **LaTeXTrans** 生成的四个真实翻译样例，左侧为原文，右侧为译文。

### 📄 样例 1 ( 英文->中文 ) :

<table>
  <tr>
    <td align="center"><b>原文</b></td>
    <td align="center"><b>译文</b></td>
  </tr>
  <tr>
    <td><img src="examples/case1src.png" width="100%"></td>
    <td><img src="examples/case1ch.png" width="100%"></td>
  </tr>
</table>

### 📄 样例 2 ( 英文->中文 ):

<table>
  <tr>
    <td align="center"><b>原文</b></td>
    <td align="center"><b>译文</b></td>
  </tr>
  <tr>
    <td><img src="examples/case3src.png" width="100%"></td>
    <td><img src="examples/case3ch.png" width="100%"></td>
  </tr>
</table>

### 📄 样例 3 ( 英文->日文 ):

<table>
  <tr>
    <td align="center"><b>原文</b></td>
    <td align="center"><b>译文</b></td>
  </tr>
  <tr>
    <td><img src="examples\case-en.png" width="100%"></td>
    <td><img src="examples\case-jp.png" width="100%"></td>
  </tr>
</table>

### 📄 样例 4 ( 英文->日文 ):

<table>
  <tr>
    <td align="center"><b>原文</b></td>
    <td align="center"><b>译文</b></td>
  </tr>
  <tr>
    <td><img src="examples\case5a-1-en.png" width="100%"></td>
    <td><img src="examples\case5b-1-jp.png" width="100%"></td>
  </tr>
</table>

📂 **更多样例请查看[`examples/`](examples/) 文件夹**, 包含每个样例的完整翻译 PDF。

---
## 致谢

感谢为本系统做出贡献的同学：
徐浩淞，刘奕扬，张恒，李晨阳，黄小茹，张一航，刘策，张硕昌，于济航，刘博扬。


---

## Citation
```bash
@article{zhu2025latextrans,
  title={LaTeXTrans: Structured LaTeX Translation with Multi-Agent Coordination},
  author={Zhu, Ziming and Wang, Chenglong and Xing, Shunjie and Huo, Yifu and Tian, Fengning and Du, Quan and Yang, Di and Zhang, Chunliang and Xiao, Tong and Zhu, Jingbo},
  journal={arXiv preprint arXiv:2508.18791},
  year={2025}
}
