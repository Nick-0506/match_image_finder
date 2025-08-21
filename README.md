# Match Image Finder

基於排序機制的重複圖片檢測工具

## 功能特色
- 支援多平台：相容 macOS 與 windows
- 節省時間：快速找出相同照片
- 自己決定：重複圖片是否保留由你親自決定，避免電腦錯刪
- 注重隱私：所有運算都在自己電腦進行，不需上傳圖片至雲端
- 自動儲存進度：比對結果自動保存

## 安裝與執行
支援以下兩種方式：
### 1. 以 Python 環境執行
支援 macOS 與 Windows。  
在終端機執行以下指令：

```bash
pip install -r Match_Image_Finder_requirements.txt
python Match_Image_Finder.py
```
### 2. 使用打包好的應用程式
* macOS：雙擊
Match_Image_Finder.app（目前僅支援 Intel CPU 版本）
* Windows：雙擊
Match_Image_Finder.exe

## 如何操作
請參照🔗 [繁體中文使用說明](./doc/Match_Image_Finder_Guide-tw.md)

## 授權方式
本專案採用 GPL License。你可以自由使用、修改、散佈本程式碼，但須遵守 GPL 條款。

## 版本歷史
### v1.0.0 2025‑08‑21
* 初版釋出，支援 phash 比對、GUI 操作等。

* 執行檔下載 macOS版：[x86_64](https://github.com/Nick-0506/match_image_finder/releases/download/v1.0.0/Match_Image_Finder_v1.0.0.app.zip) ; Windows版：[x86_64](https://github.com/Nick-0506/match_image_finder/releases/download/v1.0.0/Match_Image_Finder_v1.0.0.exe)
