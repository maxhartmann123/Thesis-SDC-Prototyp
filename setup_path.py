# setup_path.py
import sys
import site

# Add the system Python's site-packages to the path
system_site_packages = r'C:\Users\mahartmann\AppData\Local\Programs\Python\Python311\Lib\site-packages'
if system_site_packages not in sys.path:
    sys.path.insert(0, system_site_packages)
