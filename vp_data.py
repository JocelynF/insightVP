import sys,os,time,datetime,pickle
from statsmodels.tsa.seasonal import seasonal_decompose
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.automap import automap_base
from sqlalchemy.orm import Session
from sqlalchemy import Column, Integer, Float,String, DateTime,MetaData,Table
from sqlalchemy.ext.declarative import declarative_base
from pv_database import DBsession
import pandas as pd

tracked_types_csv_file="tracked_types_cats.csv"


class DataProc():
    def __init__(self,mysql_login,mysql_pass,mysql_host,mysql_port,vinepair_database):
        #assume initialized database for now
        self.dbsession = DBsession(mysql_login,mysql_pass,mysql_host,mysql_port,vinepair_database)
        self.dbsession.index_columns()
        self.dbe = self.dbsession.session.execute
        self.index_mat = None
        self.data_cache = {}

    def db_init(self):
        check_tables = list(tup[0] for tup in self.dbe("SHOW TABLES").fetchall())
        for table in ('pindex','pterms','tindex','ttype'):
            if table not in check_tables:
                print "UPDATING DB",table
                self.dbsession.ingest_wordpress_tax(wp_database)
        #get min/max dates and other date variables
        self.first_dt = self.dbe("SELECT MIN(date) FROM pagedata").first()[0]
        self.last_dt = self.dbe("SELECT MAX(date) FROM pagedata").first()[0]
        print "DATABASE contains data from %s to %s" % (self.first_dt.strftime("%Y-%m-%d"),
                                                        self.last_dt.strftime("%Y-%m-%d"))
        records = self.dbe("SELECT * FROM pagedata LIMIT 1").first()
        if records is not None:
            self.n_days = (self.last_dt - self.first_dt).days + 1
            self.dt_list = list(self.first_dt + datetime.timedelta(days=i) for i in range(self.n_days))
        else:
            self.n_days = 0
            self.dt_list,self.np_dt_list = [],[]
        #get unique keys/columns
        self.data_keys = []
        key_query = self.dbe("SELECT DISTINCT `key` FROM pagedata")
        for key in key_query:
            print "DATA available for key:",key[0]
            self.data_keys.append(key[0].strip())



    def timeseries_as_numpy(self):
        date_str =  list(dt.strftime("%Y-%m-%d") for dt in self.dt_list)
        np_cols=["pindex","key"] + date_str
        np_fmt=[np.int64,'S256'] + list(np.float32 for i in range(self.n_days))
        np_dtype = np.dtype(zip(np_cols,np_fmt))
        np_data = np.zeros(len(self.pterm_lookup.keys())*len(self.data_keys),dtype=np_dtype)
        counter=0
        for key in self.data_keys:
            for pindex in self.pterm_lookup.keys():
                dates,values = self.get_timeseries_pindex(pindex,key)
                dv_list = zip(dates,values)
                for date,value in dv_list:
                    col = date.strftime("%Y-%m-%d")
                    np_data[counter][col] = value
                counter = counter+1
                print counter
                if counter == 1000:
                    print np_data
                    sys.exit()
            
            
    def get_timeseries_pindex(self,pindex,key):
        cur_pindex_data = self.data_cache.get(pindex,{})
        if key in cur_pindex_data.keys():
            print "Fetching timeseries from cache"
            return cur_pindex_data[key]
        else:
            sql_in = 'SELECT date,SUM(count) AS total FROM pagedata WHERE pindex=%s AND `key`="%s" GROUP BY date' % (pindex,key)
            print sql_in
            query = self.dbe(sql_in)
            dates,values = [],[]
            for result in query:
                dates.append(result[0])
                values.append(result[1])
            cur_pindex_data[key] = (dates,values)
            return dates,values
        
    def get_sumseries_plist(self,plist,key):
        plist_sql = "("+",".join(list("%d" % pindex for pindex in plist))+")"
        sql_in = 'SELECT date,SUM(count) AS total FROM pagedata WHERE pindex IN %s AND `key`="%s" GROUP BY date' % (plist_sql,key)
        query = self.dbe(sql_in)
        dates,values = [],[]

        for result in query:
            dates.append(result[0])
            values.append(result[1])
        return dates,values

    
    def aggregate_by_plist(self,plist,key):
        n_rows = len(plist)
        n_col = self.n_days
        np_data = np.zeros((n_rows,n_col),dtype=np.float64)
        np_dt_list = np.array(self.dt_list,dtype='datetime64[D]')
        #in case of missing data, match indices to master date list, rest will be zero
        for row_index,pindex in enumerate(plist):
            dates,values = self.get_timeseries_pindex(pindex,key)
            np_dates = np.array(dates,dtype='datetime64[D]')
            dv_list = zip(np_dates,values)
            for date,value in dv_list:
                coli = np.nonzero(np_dt_list==date)[0][0]
                np_data[row_index,coli] = value
        total = np.nansum(np_data,axis=0)
        output_dates = list(ndt.tolist() for ndt in np_dt_list)
        return output_dates,list(total)


    def get_index_matrix(self):
        if self.dbsession.pindex_lookup is None:
            self.dbsession.create_lookups()
        pindex_list = self.dbsession.pterm_lookup.keys()
        pindex_list.sort()
        tindex_list = []
        for pindex,tlist in self.dbsession.pterm_lookup.iteritems():
            tindex_list = list(set(tindex_list + tlist))
        tindex_list.sort()
        n_rows = len(pindex_list)
        n_col = len(tindex_list)
        imatrix = np.zeros((n_rows,n_col),dtype=np.bool_)
        p2i = {}
        i2p = {}
        t2i = {}
        i2t = {}
        for i,pindex in enumerate(pindex_list):
            p2i[pindex] = i
            i2p[i] = pindex
        for i,tindex in enumerate(tindex_list):
            t2i[tindex] = i
            i2t[i] = tindex
        for pindex,tlist in self.dbsession.pterm_lookup.iteritems():
            rowi = p2i[pindex]
            for tindex in tlist:
                coli = t2i[tindex]
                imatrix[rowi,coli] = True
        self.index_mat = imatrix
        self.p2i = p2i
        self.i2p = i2p
        self.t2i = t2i
        self.i2t = i2t


    def get_plist(self,tindex):
        if self.index_mat is None:
            self.get_index_matrix()
        ti = self.t2i[tindex]
        pi_list = list(np.nonzero(self.index_mat[:,ti] == True)[0])
        plist = list(self.i2p[i] for i in pi_list)
        return plist
        
    def get_tlist(self,pindex):
        if self.index_mat is None:
            self.get_index_matrix()
        pi = self.p2i[pindex]
        ti_list = list(np.nonzero(self.index_mat[pi] == True)[0])
        tlist = list(self.i2t[i] for i in ti_list)
        print tlist

        
class TimeSeries():
    def __init__(self,dates,values):
        if len(dates) != len(values):
            print "WARNING: date/value mismatch!"
            #if len(dates) > len(values):
            #    for i in range(len(dates) - len(values)):
            #        values.append(0.0) # zero pad 
            #if len(values) > len(dates):
            #    values=values[0:len(dates)] # truncate
        dat = zip(dates,values)
        dat.sort(key = lambda x:x[0])
        dates = list(dv[0] for dv in dat)
        values = list(dv[1] for dv in dat)

        self.set_dates(dates)
        self.set_data(values)
    def set_dates(self,dt_list):
        if len(dt_list) == 0:
            self.dates = []
            self.missing = []
            return
        self.start_dt = dt_list[0]
        self.end_dt = dt_list[-1]
        self.n_days = (self.end_dt - self.start_dt).days + 1
        self.dates = self.get_all_dates() #fill in all dates
        self.missing = np.zeros(self.n_days,dtype=np.bool_)
        for dti,dt in enumerate(self.dates):
            if dt not in dt_list:
                self.missing[dti] = True
                
    def set_data(self,values):
        if len(values) == 0:
            self.data=[]
            return
        data = np.ones(self.missing.shape[0]) * np.nan
        data[np.invert(self.missing)] = values
        self.data = list(data)
 
    def get_all_dates(self):
        dt_full =list(self.start_dt + datetime.timedelta(days=i) for i in range(self.n_days))
        return dt_full

    def median_filter(self,threshold=5):
        signal = np.array(self.data)
        difference = np.abs(signal - np.median(signal))
        median_difference = np.median(difference)
        if median_difference == 0:
            s = 0
        else:
            s = difference / float(median_difference)
        mask = s > threshold
        print "FILTER",np.count_nonzero(mask)
        signal[mask] = np.median(signal)
        return list(signal)

    def rolling_median_filter(self,window=11,sigcut=10):
        if len(self.data) == 0:
            return []
        rolling_median=[]
        np_dat = np.array(self.data)
        tails = int(window/2)
        for index_window in range(0,len(self.data)-2*tails,1):
            win_dat = np_dat[index_window:index_window+window]
            win_med = float(np.nanmedian(win_dat))
            rolling_median.append(win_med)
            if index_window < tails:
                rolling_median.append(win_med)
        for i in range(tails):
            rolling_median.append(win_med)
        rm_np = np.array(rolling_median)
        diff = np_dat - rm_np #only positive differences (spikes)
        threshold = np.nanstd(self.data) * sigcut
        mask = diff > threshold
        output = np.array(self.data)
        output[mask] = rm_np[mask]
        return list(output)
    
    def fill_nan(self,list_in):
        dat = np.array(list_in)
        mask = dat == np.nan
        median = np.nanmedian(dat)
        dat[mask] = median
        return list(dat)
        


    def show(self):
        n_days = (self.end_dt - self.start_dt).days + 1
        dates = list((self.start_dt + datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n_days))
        print "%12s %12s %6s %6s" % ("   DATE   ","  Value  "," Missing "," Invalid ")
        missing = self.missing
        for i in range(n_days):
            print "%12s %12f %6s" % (dates[i],self.data[i],missing[i])

        
class AggScheme():
    def __init__(self):
        self.scheme = {"name":"Null",
                       "groups":[]}
            
    def get_agg_scheme(self,scheme_name,pterm_lookup,csv_in):
        self.pterm_lookup = pterm_lookup
        self.scheme["name"] = scheme_name
        """
        Reads in a csv file with the following format:
        group_name,tracked_tag1,tracked_tag2,tracked_tag3, etc.
        returns dict tindex --> group_name

        Generates dict of tracked pages for each tindex in above
        dict tindex --> list of tracked pindex
        """
        track_lookup={}
        track_f = open(csv_in,'r')
        for ln,line in enumerate(track_f):
            if ln > 0:
                fields = line.split(',')
                if len(fields) > 1:
                    cat = fields[0]
                    ids = fields[1::]
                    for tindex in ids:
                        tdig=""
                        for letter in tindex:
                            if letter.isdigit():
                                tdig=tdig+letter
                        if len(tdig)>0:
                            track_lookup[int(tdig)] = cat.replace('"','').strip()
        track_f.close()
        self.tracked_tindex = track_lookup.keys()
        
        #get tracked pages
        #dict tindex-->list of pindexes
        self.tracked_tindex_pages={}
        for pindex,tlist in self.pterm_lookup.iteritems():
            for tindex in tlist:
                if tindex in self.tracked_tindex:
                    plist = self.tracked_tindex_pages.get(tindex,[])
                    plist.append(pindex)
                    self.tracked_tindex_pages[tindex] = plist
        for tindex,tlist in self.tracked_tindex_pages.iteritems():
            group_name = track_lookup[tindex]
            tracked_pages = tlist
            if len(self.scheme["groups"]) == 0:
                gdict = {"group_name":group_name,"group_pages":tracked_pages}
                self.scheme["groups"].append(gdict)
            else:
                existing_groups = list(gdict["group_name"] for gdict in self.scheme["groups"])
                if group_name in existing_groups:
                    for gdict in self.scheme["groups"]:
                        if gdict["group_name"] == group_name:
                            gdict["group_pages"] = gdict["group_pages"] + tracked_pages
                else:
                    gdict = {"group_name":group_name,"group_pages":tracked_pages}
                    self.scheme["groups"].append(gdict) 
                    

    def show(self):
        scheme_name = self.scheme["name"]
        n_groups = len(self.scheme["groups"])
        print "Scheme %s contains %d groups:" % (scheme_name,n_groups)
        for gn,gdict in enumerate(self.scheme["groups"]):
            group_name = gdict["group_name"]
            group_pages = gdict["group_pages"]
            n_pages = len(group_pages)
            print "    Group: %20s  contains %5d pages." % (group_name,n_pages) 
                       
            
    def filter(self,plist):
        filter_set = set(plist)
        output_list = []
        for gdict in self.scheme["groups"]:
            gname = gdict['group_name']
            input_pages = gdict['group_pages']
            output_pages = list(filter_set.intersection(set(gdict['group_pages'])))
            if len(output_pages) > 0:
                output_list.append({'group_name':gname,'group_pages':output_pages})
        self.scheme["groups"] = output_list
