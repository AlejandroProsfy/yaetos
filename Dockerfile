FROM jupyter/pyspark-notebook
# TODO: build spark image from vanilla ubuntu (or other), see https://github.com/masroorhasan/docker-pyspark
# FROM arthurpr/pyspark_aws_etl:latest
# FROM arthurpr/pyspark_aws_etl:oracle # also available to skip oracle install steps below.
USER root

# Pip installs. Using local copy to tmp dir to allow checkpointing this step (no re-installs as long as requirements.txt doesn't change)
COPY requirements.txt /tmp/requirements.txt
WORKDIR /tmp/
RUN pip3 install -r requirements.txt

WORKDIR /mnt/pyspark_aws_etl

RUN mkdir -p tmp/files_to_ship/
ENV PYSPARK_AWS_ETL_HOME /mnt/pyspark_aws_etl/
ENV PYTHONPATH $PYSPARK_AWS_ETL_HOME:$PYTHONPATH
# ENV SPARK_HOME /usr/local/spark # already set in base docker image
ENV PYTHONPATH $SPARK_HOME/python:$SPARK_HOME/python/build:$PYTHONPATH

# Expose ports for monitoring.
# SparkContext web UI on 4040 -- only available for the duration of the application.
# Spark master’s web UI on 8080.
# Spark worker web UI on 8081.
EXPOSE 4040 8080 8081

CMD ["/bin/bash"]

# Usage: docker run -it -p 4040:4040 -p 8080:8080 -p 8081:8081 -v ~/code/pyspark_aws_etl:/mnt/pyspark_aws_etl -v ~/.aws:/root/.aws -h spark <image_id>
