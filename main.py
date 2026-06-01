from BES_pipeline import BESPipeline

from database_agent.ingest_pipeline import (
    IngestPipeline
)


def main():

    print("=== STEP 1: BES SCRAPING ===")

    bes_pipeline = BESPipeline()
    bes_pipeline.run()

    #print("=== STEP 2: DATABASE INGESTION ===")

    #ingest_pipeline = IngestPipeline()
    #ingest_pipeline.run()

    print("=== PIPELINE COMPLETE ===")


if __name__ == "__main__":
    main()