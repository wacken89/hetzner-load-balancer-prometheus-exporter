FROM python:3.12-slim
 
RUN addgroup --gid 11000 app && \
    adduser --uid 11001 --disabled-login --gid 11000 --home /code app
 
COPY code /code
 
RUN pip install --no-cache-dir -r /code/requirements.txt
 
WORKDIR /code
 
ENV PYTHONPATH='/code/'
ENV PYTHONUNBUFFERED=1
 
EXPOSE 8000
 
USER 11001
 
CMD ["python", "-u", "/code/exporter.py"]
 