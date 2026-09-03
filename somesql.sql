--Not TCS
select DISTINCT(opportunity_id) from interview_round where opportunity_id not in (
select id from opportunity where imp_partner_id = 1 and job_description is not null) ORDER by opportunity_id;



select ec.name, v.name, o.* from opportunity o join vendor v on v.id=o.vendor_id JOIN end_client ec on ec.id=o.end_client_id where job_description is not null and employment_type = "Full-time";

--Not Fulltime 
select ec.name, v.name, o.* from opportunity o join vendor v on v.id=o.vendor_id JOIN end_client ec on ec.id=o.end_client_id where job_description is not null and employment_type != "Full-time";
