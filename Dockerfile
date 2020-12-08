FROM python:3.8.6

ADD code /code
RUN pip install -r /code/requirements.txt

WORKDIR /code
ENV PYTHONPATH '/code/'

EXPOSE 8000

CMD ["python" , "-u", "/code/collector.py"]