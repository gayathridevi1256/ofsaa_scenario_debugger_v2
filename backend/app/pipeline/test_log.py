import sys, os, json
sys.path.insert(0, '.')
sys.path.insert(0, 'pipeline')

from log_reader import _read_uploaded_text, _extract_log_metadata, _extract_all_queries_from_log, _is_function_scenario
from path_manager import create_output_directory

log_path = r'D:\One Drive\OneDrive - JMR Infotech India (P) Ltd\D Drive Data\hari\project\Scenario_Debugger\ML-StructuringAvoidReportThreshold_(ML-StructuringAvoidReportThreshold)_AIIN.log'

log_text = _read_uploaded_text(log_path)
metadata = _extract_log_metadata(log_text)

print(f"Scenario: {metadata.get('job_description')}")
print(f"Date: {metadata.get('current_business_date')}")
print(f"SCNRO_ID: {metadata.get('scnro_id')}")
print(f"TSHLD_SET_ID: {metadata.get('tshld_set_id')}")
multi_query = _is_function_scenario(metadata)
print(f"Function scenario: {multi_query}")

queries = _extract_all_queries_from_log(log_text, multi_query=True)
print(f"\nReference queries: {len(queries['main_queries'])}")
print(f"Dataset queries: {len(queries['dataset_queries'])}")
for dq in queries['dataset_queries']:
    print(f"  Dataset ID: {dq['dataset_id']} ({len(dq['sql'])} chars)")
