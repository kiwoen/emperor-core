下面用 Python 读取一个大文件并逐行处理，避免一次性读入内存：

```python
def iter_lines(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            yield line.rstrip("\n")
```

该写法适合日志、CSV 等流式场景，内存占用恒定。配合 `for line in iter_lines(p):` 即可
逐行处理任意大小的文件，无需担心内存溢出。
