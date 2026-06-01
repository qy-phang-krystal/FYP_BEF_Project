import pandas as pd
import os

from llm_screening_gpt import llm_screening
from suppdata import (
    flatten_and_format,
    count_successful_files,
    suppdata
)


class BESPipeline:
    def run(self):

        # STEP 1 — LLM SCREENING
        #llm_screening(
        #    excel="UK_BEF_data.xlsx",
        #    num_rows=52,
        #)

        # STEP 2 — LOAD INCLUDED PAPERS
        target_papers = (
            pd.read_csv("screening_results_GPT_final.csv")
            .loc[
                lambda df:
                df["LLM_decision"] == "Include uk",
                "DOI",
            ]
            .tolist()
        )

        print(
            f"\n[+] Found "
            f"{len(target_papers)} included papers"
        )

        # STEP 3 — DOWNLOAD SUPPLEMENTARY DATA
        batch_results = suppdata(
            dois=target_papers,
            save_dir=(
                "C:\\Users\\Krystal\\OneDrive - "
                "Imperial College London\\imperial\\"
                "FYP\\FYP_Code\\Data\\raw"
            ),
        )

        # STEP 4 — PRINT RESULTS
        self.print_results(batch_results)

        # STEP 5 — EXPORT REPORT
        self.export_report(batch_results)

        return batch_results

    def print_results(self, batch_results):

        for paper, files in batch_results.items():

            print(
                "\n========================================="
            )

            print(f"Results for {paper}:")

            # -----------------------------------
            # Publisher Data
            # -----------------------------------

            pub_data = files.get(
                "publisher_file",
                "Failed"
            )

            if (
                isinstance(pub_data, list)
                and pub_data not in [
                    ["No_Data"],
                    ["Failed"]
                ]
            ):

                print("Publisher Data:")

                for item in pub_data:
                    print(f"  -> {item}")

            else:

                if isinstance(pub_data, list):
                    pub_data = pub_data[0]

                print(
                    f"Publisher Data: {pub_data}"
                )

            # -----------------------------------
            # Repository Data
            # -----------------------------------

            repo_data = files.get(
                "repository_files",
                "Failed"
            )

            if (
                isinstance(repo_data, list)
                and repo_data not in [
                    ["No_Data"],
                    ["Failed"]
                ]
            ):

                print("Repository Data:")

                for item in repo_data:
                    print(f"  -> {item}")

            else:

                if isinstance(repo_data, list):
                    repo_data = repo_data[0]

                print(
                    f"Repository Data: {repo_data}"
                )

    def export_report(self, batch_results):

        excel_data = []

        for paper_doi, files in batch_results.items():

            raw_pub = files.get(
                "publisher_file",
                "Failed"
            )

            raw_repo = files.get(
                "repository_files",
                "Failed"
            )

            pub_data_str = flatten_and_format(
                raw_pub
            )

            repo_data_str = flatten_and_format(
                raw_repo
            )

            pub_count = count_successful_files(
                raw_pub
            )

            repo_count = count_successful_files(
                raw_repo
            )

            excel_data.append({
                "DOI": paper_doi,
                "Supp Files Downloaded": pub_count,
                "Repo Files Downloaded": repo_count,
                "Supplementary Info (Publisher)": (
                    pub_data_str
                ),
                "Repository Data (External)": (
                    repo_data_str
                ),
            })

        df = pd.DataFrame(excel_data)

        save_directory = (
            "C:\\Users\\Krystal\\OneDrive - "
            "Imperial College London\\imperial\\"
            "FYP\\FYP_Code"
        )

        excel_filename = os.path.join(
            save_directory,
            "Scraping_Results_Report.xlsx"
        )

        df.to_excel(
            excel_filename,
            index=False,
        )

        print(
            f"\n[+] Report saved: "
            f"{excel_filename}"
        )