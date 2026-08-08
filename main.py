from src.main import main
if __name__ == '__main__':
    import sys
    if sys.platform == 'win32':
        import multiprocessing
        multiprocessing.freeze_support()
    raise SystemExit(main())
