FROM python:3.8-slim

COPY code /code
RUN pip install --no-cache-dir -r /code/requirements.txt

WORKDIR /code
ENV PYTHONPATH '/code/'

EXPOSE 8000

CMD ["python" , "-u", "/code/collector.py"]