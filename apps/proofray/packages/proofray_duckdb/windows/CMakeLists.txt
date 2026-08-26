cmake_minimum_required(VERSION 3.14)
project(proofray_duckdb LANGUAGES CXX)

add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/../src" "${CMAKE_CURRENT_BINARY_DIR}/native")
set(proofray_duckdb_bundled_libraries
  "${PROOFRAY_DUCKDB_LIBRARY}"
  PARENT_SCOPE)
