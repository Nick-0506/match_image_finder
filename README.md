# Match Image Finder

基於排序機制的重複圖片檢測工具

## 功能特色
- 支援多平台：相容 macOS 與 windows
- 節省時間：快速找出相同照片
- 自己決定：重複圖片是否保留由你親自決定，避免電腦錯刪
- 注重隱私：所有運算都在自己電腦進行，不需上傳圖片至雲端
- 自動儲存進度：比對結果自動保存

## 安裝與執行
支援 macOS 及 Windows（Python 3.11）  
```bash
pip install -r Match_Image_Finder_requirements.txt
python Match_Image_Finder.py
```

## 如何操作
請參照🔗 [繁體中文使用說明](./doc/Match_Image_Finder_Guide-tw.md)

## 授權方式
本專案採用 GPL License。你可以自由使用、修改、散佈本程式碼，但須遵守 GPL 條款。

## 版本歷史
### v1.0.0 2025‑08‑21
* 初版釋出，支援 phash 比對、GUI 操作等。

* 執行檔下載 macOS版：[x86_64](./release/v1.0.0/Match_Image_Finder_v1.0.0.app/) ; Windows版：[x86_64](./releases/v1.0.0/Match_Image_Finder_v1.0.0.exe)
