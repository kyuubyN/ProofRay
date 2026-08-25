cmake_minimum_required(VERSION 3.18)
project(proofray_duckdb_native LANGUAGES C CXX)

set(DUCKDB_VERSION "1.4.2")
set(DUCKDB_RELEASE "https://github.com/duckdb/duckdb/releases/download/v${DUCKDB_VERSION}")
set(DUCKDB_STAGE "${CMAKE_CURRENT_BINARY_DIR}/duckdb")
file(MAKE_DIRECTORY "${DUCKDB_STAGE}")

if(ANDROID)
  set(DUCKDB_ARCHIVE "${DUCKDB_STAGE}/libduckdb-src.zip")
  file(DOWNLOAD
    "${DUCKDB_RELEASE}/libduckdb-src.zip"
    "${DUCKDB_ARCHIVE}"
    EXPECTED_HASH SHA256=ee7e178341ea8199ad52eabdff07aa89969f9904868eaa94e71efb31eaef7f2d
    TLS_VERIFY ON
    STATUS DUCKDB_DOWNLOAD_STATUS)
  list(GET DUCKDB_DOWNLOAD_STATUS 0 DUCKDB_DOWNLOAD_CODE)
  if(NOT DUCKDB_DOWNLOAD_CODE EQUAL 0)
    message(FATAL_ERROR "Verified DuckDB Android source download failed")
  endif()
  file(ARCHIVE_EXTRACT INPUT "${DUCKDB_ARCHIVE}" DESTINATION "${DUCKDB_STAGE}/src")
  add_library(duckdb SHARED "${DUCKDB_STAGE}/src/duckdb.cpp")
  target_include_directories(duckdb PUBLIC "${DUCKDB_STAGE}/src")
  target_compile_features(duckdb PRIVATE cxx_std_11)
  target_compile_options(duckdb PRIVATE -O3 -fvisibility=hidden)
  target_link_options(duckdb PRIVATE "-Wl,-z,max-page-size=16384")
  set_target_properties(duckdb PROPERTIES OUTPUT_NAME duckdb)
elseif(WIN32)
  set(DUCKDB_ARCHIVE "${DUCKDB_STAGE}/libduckdb-windows-amd64.zip")
  file(DOWNLOAD
    "${DUCKDB_RELEASE}/libduckdb-windows-amd64.zip"
    "${DUCKDB_ARCHIVE}"
    EXPECTED_HASH SHA256=4d3e5f2ee009a8fff5b8d43bc50f61c6b246b2b227748bc900f8520ec8fac0b0
    TLS_VERIFY ON
    STATUS DUCKDB_DOWNLOAD_STATUS)
  list(GET DUCKDB_DOWNLOAD_STATUS 0 DUCKDB_DOWNLOAD_CODE)
  if(NOT DUCKDB_DOWNLOAD_CODE EQUAL 0)
    message(FATAL_ERROR "Verified DuckDB Windows download failed")
  endif()
  file(ARCHIVE_EXTRACT INPUT "${DUCKDB_ARCHIVE}" DESTINATION "${DUCKDB_STAGE}/bin")
  set(PROOFRAY_DUCKDB_LIBRARY "${DUCKDB_STAGE}/bin/duckdb.dll" PARENT_SCOPE)
else()
  set(DUCKDB_ARCHIVE "${DUCKDB_STAGE}/libduckdb-linux-amd64.zip")
  file(DOWNLOAD
    "${DUCKDB_RELEASE}/libduckdb-linux-amd64.zip"
    "${DUCKDB_ARCHIVE}"
    EXPECTED_HASH SHA256=0a68e623cbdcfa06e10e1b5d6a3092d3fd3679f616008fb1a39b92e4a10d8f43
    TLS_VERIFY ON
    STATUS DUCKDB_DOWNLOAD_STATUS)
  list(GET DUCKDB_DOWNLOAD_STATUS 0 DUCKDB_DOWNLOAD_CODE)
  if(NOT DUCKDB_DOWNLOAD_CODE EQUAL 0)
    message(FATAL_ERROR "Verified DuckDB Linux download failed")
  endif()
  file(ARCHIVE_EXTRACT INPUT "${DUCKDB_ARCHIVE}" DESTINATION "${DUCKDB_STAGE}/bin")
  set(PROOFRAY_DUCKDB_LIBRARY "${DUCKDB_STAGE}/bin/libduckdb.so" PARENT_SCOPE)
endif()
