# OCR (vendored)

`sarvam_ocr.py` and `script_detection.py` come from the teammate repo
[corefour05-code/sarvam-priority-agent](https://github.com/corefour05-code/sarvam-priority-agent) (`services/`).
Only the import path was changed. The app calls it from `app/inbox.py` when a customer sends a photo
(receipt, salary slip, note): the extracted text goes through the same claim reader as a typed message.
