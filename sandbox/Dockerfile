FROM python:3.12-slim
RUN pip install --no-cache-dir duckdb pandas numpy scipy matplotlib
# Bake matplotlib's font cache into the image; the container FS is read-only at runtime,
# so without this every call would spend ~20s rebuilding it.
ENV MPLBACKEND=Agg MPLCONFIGDIR=/opt/mpl PYTHONDONTWRITEBYTECODE=1
RUN python -c "import matplotlib.pyplot" && chmod -R a+rX /opt/mpl
WORKDIR /work
