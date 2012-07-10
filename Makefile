none:
	echo none

clean:
	rm -f bulkloader-log-* bulkloader-progress-*.sql3 bulkloader-results-*.sql3 *~


DLDIR=../scorethai-work
APPCFG=./appcfg

downloaddata:
	${APPCFG} download_data --config_file=bulkloader.yaml \
		--kind=Score --filename=${DLDIR}/score.csv \
		--url=http://scorethai.appspot.com/_ah/remote_api
	${APPCFG} download_data --config_file=bulkloader.yaml \
		--kind=ScoreData --filename=${DLDIR}/scoredata.csv \
		--url=http://scorethai.appspot.com/_ah/remote_api
